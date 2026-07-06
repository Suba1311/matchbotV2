"""The orchestrator — runs the whole pipeline end to end, no manual steps.

For each provider file it: parses, maps to canonical, cleanses + DQ, matches,
writes TARGET/ERROR, persists the audit record, and notifies. Row counts and
timings are captured at every hop into :class:`RunMetrics`. Any stage failure is
caught, recorded as a FAILED audit row, logged, and (optionally) re-raised — the
run never silently half-completes.

The stage order is wired here; stages themselves know nothing of each other.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import polars as pl

from matchbot.audit.metrics import RunMetrics
from matchbot.domain.enums import RunStatus, Stage
from matchbot.logging_setup import bind_run, get_logger
from matchbot.matching import blocking
from matchbot.matching.base import build_matchers
from matchbot.pipeline.base import PipelineContext
from matchbot.pipeline.canonical import CanonicalStage
from matchbot.pipeline.cleanse import CleanseStage
from matchbot.pipeline.match import MatchStage, matched_on_attributes, resolve_matcher_chain
from matchbot.pipeline.parse import ParseStage

if TYPE_CHECKING:
    from matchbot.config.models import AppConfig, ProviderConfig
    from matchbot.config.settings import Settings
    from matchbot.notify.base import Notifier
    from matchbot.runtime.base import FileSystem
    from matchbot.storage.base import Repository

log = get_logger(__name__)

# Provenance columns the parse stage attaches; excluded from the raw LAND dump's
# source-column set (they are tracking, not file data).
_PROVENANCE = frozenset({"source_row_id", "provider_id", "run_id"})

# Any full-file step that has to materialize rows as Python dicts (LAND write,
# MATCH) processes in fixed-size batches instead of all at once. Converting an
# entire 1M+ row frame to Python dicts (plus, for MATCH, three more parallel
# result lists) was large enough to OOM even an 8 GiB container — see
# docs/glue-implementation.md / docs/ecs-implementation.md for the incident
# history. Batching bounds peak memory to roughly one batch's worth regardless
# of file size; each batch is written to the DB immediately, so a crash
# partway through only loses the in-flight batch, not already-written ones.
_ROW_BATCH_SIZE = 50_000


@dataclass(slots=True)
class RunResult:
    """Summary returned to the caller (the CLI prints this)."""

    run_id: str
    provider_id: str
    source_uri: str
    metrics: RunMetrics


def _new_run_id() -> str:
    return f"run-{uuid.uuid4().hex[:12]}"


class Orchestrator:
    """Drives the end-to-end run for one provider's files."""

    def __init__(
        self,
        config: AppConfig,
        settings: Settings,
        repository: Repository,
        filesystem: FileSystem,
        notifier: Notifier,
    ) -> None:
        self._config = config
        self._settings = settings
        self._repo = repository
        self._fs = filesystem
        self._notifier = notifier

    def run_provider(self, provider_id: str, input_uri: str) -> list[RunResult]:
        """Process every file for ``provider_id`` found under ``input_uri``."""
        provider = self._config.provider(provider_id)
        files = self._fs.list(input_uri, provider.file_glob)
        if not files:
            log.warning(
                "run.no_files", provider=provider_id, input=input_uri, glob=provider.file_glob
            )
            return []
        log.info("run.files_found", provider=provider_id, count=len(files))
        return [self._run_one_file(provider_id, uri) for uri in files]

    def _run_one_file(self, provider_id: str, source_uri: str) -> RunResult:
        provider = self._config.provider(provider_id)
        run_uid = _new_run_id()
        dataset_name = provider.dataset_name
        metrics = RunMetrics(
            run_id=run_uid,
            provider_id=provider_id,
            runtime=self._settings.runtime,
            source_uri=source_uri,
        )
        ctx = PipelineContext(
            run_id=run_uid,
            provider=provider,
            config=self._config,
            settings=self._settings,
            repository=self._repo,
            metrics=metrics,
        )

        with bind_run(run_id=run_uid, provider=provider_id, source=source_uri):
            log.info("run.start")
            # Issue the integer pipeline_run_id up front so every table row ties
            # back to this run even if a later stage fails.
            pipeline_run_id = self._repo.begin_run(
                run_uid=run_uid,
                provider_code=provider.provider_code,
                dataset_name=dataset_name,
                runtime=self._settings.runtime,
                source_uri=source_uri,
            )
            ctx.state["pipeline_run_id"] = pipeline_run_id
            try:
                self._execute(ctx, source_uri, metrics, pipeline_run_id)
                metrics.finalize(RunStatus.SUCCESS)
            except Exception as exc:
                metrics.error = f"{type(exc).__name__}: {exc}"
                metrics.finalize(RunStatus.FAILED)
                log.error("run.failed", error=metrics.error)
                self._finalize_and_notify(pipeline_run_id, metrics)
                raise
            self._finalize_and_notify(pipeline_run_id, metrics)
            log.info(
                "run.done",
                status=metrics.status.value,
                match_rate=metrics.match_rate,
                duration_s=metrics.duration_seconds,
            )

        return RunResult(run_uid, provider_id, source_uri, metrics)

    def _execute(
        self, ctx: PipelineContext, source_uri: str, metrics: RunMetrics, pipeline_run_id: int
    ) -> None:
        """Wire and run the full stage sequence, persisting at each hop."""
        empty = pl.DataFrame()
        provider = ctx.provider

        # Stage 1 — Parse (RECEIVED = rows read).
        raw = self._fs.read_bytes(source_uri)
        with metrics.time_stage(Stage.PARSE, rows_in=0) as box:
            parsed = ParseStage(raw).run(ctx, empty).frame
            box[0] = parsed.height
        metrics.rows_received = parsed.height

        # LAND — immutable raw archive: dump every source column verbatim.
        # Source columns are the parsed columns minus pipeline provenance.
        # Batched for the same reason as MATCH below: parsed.to_dicts() on the
        # full frame (plus write_land's own per-row cleaning pass) held two
        # more full-size Python-object copies of a 1M+ row file in memory at
        # once — this was the actual cause of ECS OOMs at 2-8 GiB, since it
        # runs before cleanse/canonical/stage/match are ever reached (crashes
        # show up right after "parse.done" with nothing further logged).
        source_columns = [c for c in parsed.columns if c not in _PROVENANCE]
        landed = 0
        for batch in parsed.iter_slices(n_rows=_ROW_BATCH_SIZE):
            landed += self._repo.write_land(
                pipeline_run_id=pipeline_run_id,
                provider_code=provider.provider_code,
                source_columns=source_columns,
                rows=batch.to_dicts(),
            )
        metrics.rows_landed = landed

        # Stage 3 — Map to Canonical.
        with metrics.time_stage(Stage.CANONICAL, rows_in=parsed.height) as box:
            canonical = CanonicalStage().run(ctx, parsed).frame
            box[0] = canonical.height

        # Stage 2 — Cleanse & DQ (adds derived blocking columns).
        with metrics.time_stage(Stage.CLEANSE, rows_in=canonical.height) as box:
            cleansed = CleanseStage().run(ctx, canonical).frame
            box[0] = cleansed.height
        metrics.rows_cleansed = cleansed.height

        # STAGE — insert canonical+blocking rows (PENDING), capture stage ids.
        # Batched for the same reason as LAND above: _stage_rows()'s
        # frame.to_dicts() over the full file, plus write_stage()'s own
        # per-row cleaning pass, is the same "whole file as Python objects"
        # risk. STAGE and MATCH are batched together here (not as two
        # separate passes) since each stage-row batch's returned ids feed
        # straight into that same batch's match — no reason to write all of
        # STAGE, rebuild a full staged frame, then re-slice it for MATCH.
        g = ctx.config.global_config
        keys = g.matching.blocking_keys
        if ctx.provider.matchers:
            chosen = resolve_matcher_chain(ctx.provider.matchers, g.matching.matchers)
        else:
            chosen = list(g.matching.matchers)
        matchers = build_matchers(chosen, g.standardization)
        metrics.matched_on = matched_on_attributes(chosen)
        members = self._repo.load_member_universe()
        index = blocking.index_members(members, keys)

        # STAGE-row writes happen inline within this timed MATCH block (not
        # under a separate Stage.STAGE timing) — same as before this change,
        # where the STAGE write sat between the CLEANSE and MATCH timers,
        # untimed on its own. Only the batching is new; the timing contract
        # (stage_timings covers parse/canonical/cleanse/match) is unchanged.
        rows_staged = 0
        matched_count = 0
        match_stage = MatchStage()
        with metrics.time_stage(Stage.MATCH, rows_in=cleansed.height) as match_box:
            for chunk in cleansed.iter_slices(n_rows=_ROW_BATCH_SIZE):
                stage_rows = self._stage_rows(chunk, provider)
                stage_ids = self._repo.write_stage(pipeline_run_id, stage_rows)
                rows_staged += len(stage_ids)

                batch = chunk.with_columns(
                    pl.Series("id", stage_ids),
                    pl.lit(pipeline_run_id).alias("pipeline_run_id"),
                )
                result = match_stage.run(ctx, batch, members, index, matchers, keys)
                self._repo.update_stage_matches(result.side_outputs["stage_updates"])
                self._repo.write_target(result.side_outputs["target"])
                self._repo.write_error(result.side_outputs["error"])
                matched_count += len(result.side_outputs["target"])
            match_box[0] = matched_count
        metrics.rows_staged = rows_staged

    @staticmethod
    def _stage_rows(frame: pl.DataFrame, provider: ProviderConfig) -> list[dict[str, Any]]:
        """Project the cleansed frame to stage-table columns.

        The cleanse stage already populates ``sasid`` from the canonical
        ``member_external_id``; here we only add the per-run tracking columns.
        """
        rows: list[dict[str, Any]] = []
        for r in frame.to_dicts():
            rows.append(
                {
                    **r,
                    "provider_code": provider.provider_code,
                    "dataset_name": provider.dataset_name,
                    "match_status": "PENDING",
                }
            )
        return rows

    def _finalize_and_notify(self, pipeline_run_id: int, metrics: RunMetrics) -> None:
        """Write the run-log row and fire the completion notifier (best-effort)."""
        try:
            self._repo.finalize_run(pipeline_run_id, metrics)
        except Exception as exc:
            log.error("run.finalize_failed", error=str(exc))
        try:
            self._notifier.notify(metrics)
        except Exception as exc:
            log.error("notify_failed", error=str(exc))
