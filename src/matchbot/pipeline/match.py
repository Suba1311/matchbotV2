"""Stage 4 — Match vs Member Universe.

Operates on the STAGE frame (each row already carries its integer ``id`` from
the stage insert). Builds a blocking index over the Member Universe, runs each
staged record through the configured matcher chain, and produces three outputs:

* ``stage_updates`` — in-place updates to stage rows (member_id/score/status).
* ``target``        — matched rows (stage_id, member_id, score, method).
* ``error``         — unmatched / low-confidence rows (stage_id, decision, reason).

The first matcher to reach a terminal decision (MATCHED or AMBIGUOUS) wins. No
human gate: AMBIGUOUS -> LOW_CONFIDENCE in the error table for optional review.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import polars as pl

from matchbot.domain.canonical import MATCH_ATTRIBUTE_COLUMNS
from matchbot.domain.enums import MatchDecision, Stage
from matchbot.logging_setup import get_logger
from matchbot.matching import blocking
from matchbot.matching.vocab import (
    STATUS_LOW_CONFIDENCE,
    STATUS_MATCHED,
    STATUS_NO_MATCH,
    method_to_db,
)
from matchbot.pipeline.base import PipelineContext, StageResult

if TYPE_CHECKING:
    from matchbot.config.models import BlockingKey, MatcherSpec
    from matchbot.matching.base import Matcher

log = get_logger(__name__)


def resolve_matcher_chain(
    provider_matchers: list[str | MatcherSpec],
    global_specs: list[MatcherSpec],
) -> list[MatcherSpec]:
    """Build the ordered matcher chain for a provider.

    Each entry in ``provider_matchers`` is either:
    - a string  → reference to a global matcher by name (must exist)
    - MatcherSpec → inline local definition; overrides a global matcher of the
      same name if one exists, otherwise adds a provider-only matcher

    The returned list preserves the provider's declared order. Called once per
    run by the orchestrator (not per batch) since the resolved chain is the
    same for every batch of a given provider's file.
    """
    global_by_name = {s.name: s for s in global_specs}
    resolved: list[MatcherSpec] = []
    for entry in provider_matchers:
        if isinstance(entry, str):
            resolved.append(global_by_name[entry])
        else:
            resolved.append(entry)
    return resolved


class MatchStage:
    """Block, score, and route staged records to stage updates + target/error.

    Runs against one batch of staged records at a time — the caller (the
    orchestrator) is responsible for chunking a large staged frame and for
    loading/indexing the Member Universe once, up front, rather than per
    batch. This keeps peak memory bounded by batch size instead of file size:
    at 1M+ staged rows, materializing the whole file as Python dicts plus
    three parallel result lists (stage_updates/target/error) was large enough
    to OOM even an 8 GiB container — see docs/glue-implementation.md and
    docs/ecs-implementation.md for the incident history.
    """

    stage = Stage.MATCH

    def run(
        self,
        ctx: PipelineContext,
        frame: pl.DataFrame,
        members: list[dict[str, Any]],
        index: dict[str, list[int]],
        matchers: list[Matcher],
        keys: list[BlockingKey],
    ) -> StageResult:
        records = frame.to_dicts()
        stage_updates: list[dict[str, Any]] = []
        target_rows: list[dict[str, Any]] = []
        error_rows: list[dict[str, Any]] = []

        for rec in records:
            stage_id = rec.get("id")
            cand_idx = blocking.candidate_indices(rec, keys, index)
            candidates = [members[i] for i in cand_idx]

            outcome = None
            matcher_name = ""
            for matcher in matchers:
                result = matcher.match(rec, candidates)
                if result.decision in (MatchDecision.MATCHED, MatchDecision.AMBIGUOUS):
                    outcome = result
                    matcher_name = matcher.name
                    break

            if outcome is not None and outcome.decision is MatchDecision.MATCHED:
                member_id = self._member_pk(outcome.member_id)
                stage_updates.append(
                    {
                        "id": stage_id,
                        "member_id": member_id,
                        "match_score": outcome.score,
                        "match_status": STATUS_MATCHED,
                    }
                )
                target_rows.append(
                    {
                        "pipeline_run_id": rec.get("pipeline_run_id"),
                        "stage_id": stage_id,
                        "member_id": member_id,
                        "match_score": outcome.score,
                        "match_method": method_to_db(outcome.method, matcher_name),
                        **_match_attributes(rec),
                    }
                )
            else:
                decision = outcome.decision if outcome else MatchDecision.UNMATCHED
                status = (
                    STATUS_LOW_CONFIDENCE
                    if decision is MatchDecision.AMBIGUOUS
                    else STATUS_NO_MATCH
                )
                stage_updates.append(
                    {
                        "id": stage_id,
                        "member_id": None,
                        "match_score": outcome.score if outcome else 0.0,
                        "match_status": status,
                    }
                )
                error_rows.append(
                    {
                        "pipeline_run_id": rec.get("pipeline_run_id"),
                        "stage_id": stage_id,
                        "decision": status,
                        "match_score": outcome.score if outcome else 0.0,
                        "reason": outcome.reason if outcome else "no candidate matched",
                        **_match_attributes(rec),
                    }
                )

        # += not = : this runs once per batch, so the orchestrator's totals
        # must accumulate across calls rather than being overwritten each time.
        batch_ambiguous = sum(1 for r in error_rows if r["decision"] == STATUS_LOW_CONFIDENCE)
        batch_unmatched = len(error_rows) - batch_ambiguous
        ctx.metrics.rows_matched += len(target_rows)
        ctx.metrics.rows_ambiguous += batch_ambiguous
        ctx.metrics.rows_unmatched += batch_unmatched

        log.info(
            "match.batch_done",
            staged=len(records),
            matched=len(target_rows),
            unmatched=batch_unmatched,
            ambiguous=batch_ambiguous,
            candidates_indexed=len(members),
        )

        return StageResult(
            frame=frame,
            side_outputs={
                "stage_updates": stage_updates,
                "target": target_rows,
                "error": error_rows,
            },
        )

    @staticmethod
    def _member_pk(member_id: str | None) -> int | None:
        """Member id from the matcher is the member_universe.id (as str). To int."""
        if member_id is None:
            return None
        try:
            return int(member_id)
        except (TypeError, ValueError):
            return None


def _match_attributes(rec: dict[str, Any]) -> dict[str, Any]:
    """Extract the matching-attribute values from a staged record.

    These are denormalized onto target/error rows so each row is
    self-explanatory — you can see the attributes that were used to match (or
    that were present when the match failed) without joining back to stage.
    """
    return {col: rec.get(col) for col in MATCH_ATTRIBUTE_COLUMNS}
