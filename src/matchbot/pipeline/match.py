"""Stage 4 — Match vs the rilds_reference identity reference.

Operates on the STAGE frame (each row already carries its integer ``id`` from
the stage insert). Builds a blocking index over ``rilds_reference``, runs each
staged record through the configured matcher chain, and produces three outputs:

* ``stage_updates`` — in-place updates to stage rows (idcol_id/score/status).
* ``target``        — matched rows (stage_id, idcol_id, score, method).
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


# Canonical attribute -> display name, for reporting (e.g. the email summary)
# only — never used in matching logic itself. Falls back to a title-cased,
# underscore-stripped version of the raw attribute for anything not listed
# here, so a new canonical attribute doesn't require a code change to show up
# reasonably in reports; add an entry here only to improve its display label.
_ATTRIBUTE_DISPLAY_NAMES: dict[str, str] = {
    "member_external_id": "SASID",
    "first_name": "First Name",
    "first_name_std": "First Name",
    "middle_name": "Middle Name",
    "last_name": "Last Name",
    "last_name_std": "Last Name",
    "birth_date": "Birth Date",
    "ssn": "SSN",
    "gender": "Gender",
}


def _display_name(attribute: str) -> str:
    return _ATTRIBUTE_DISPLAY_NAMES.get(attribute, attribute.replace("_", " ").title())


def matched_on_attributes(matcher_chain: list[MatcherSpec]) -> list[str]:
    """Human-readable, deduplicated attribute names the chain compares on.

    Deterministic matchers compare ``keys`` exactly; fuzzy matchers compare
    each ``comparisons[].attribute`` by similarity. Order follows the chain's
    declared order, first-seen; purely for reporting (e.g. the completion
    email) — never consulted by matching logic itself.
    """
    seen: dict[str, None] = {}
    for spec in matcher_chain:
        for attr in spec.keys:
            seen.setdefault(_display_name(attr), None)
        for comparison in spec.comparisons:
            seen.setdefault(_display_name(comparison.attribute), None)
    return list(seen)


class MatchStage:
    """Block, score, and route staged records to stage updates + target/error.

    Runs against one batch of staged records at a time — the caller (the
    orchestrator) is responsible for chunking a large staged frame and for
    loading/indexing rilds_reference once, up front, rather than per batch.
    This keeps peak memory bounded by batch size instead of file size:
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
        reference_rows: list[dict[str, Any]],
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
            candidates = [reference_rows[i] for i in cand_idx]

            outcome = None
            matcher_name = ""
            for matcher in matchers:
                result = matcher.match(rec, candidates)
                if result.decision in (MatchDecision.MATCHED, MatchDecision.AMBIGUOUS):
                    outcome = result
                    matcher_name = matcher.name
                    break

            if outcome is not None and outcome.decision is MatchDecision.MATCHED:
                idcol_id = self._reference_pk(outcome.idcol_id)
                stage_updates.append(
                    {
                        "id": stage_id,
                        "idcol_id": idcol_id,
                        "match_score": outcome.score,
                        "match_status": STATUS_MATCHED,
                    }
                )
                target_rows.append(
                    {
                        "pipeline_run_id": rec.get("pipeline_run_id"),
                        "stage_id": stage_id,
                        "idcol_id": idcol_id,
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
                        "idcol_id": None,
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
        # AMBIGUOUS (low-confidence) records are still routed to the error
        # table with their own decision/reason (see STATUS_LOW_CONFIDENCE
        # below) — only the separate rows_ambiguous *count* was removed, so
        # they're folded into rows_unmatched for reporting purposes.
        ctx.metrics.rows_matched += len(target_rows)
        ctx.metrics.rows_unmatched += len(error_rows)

        log.info(
            "match.batch_done",
            staged=len(records),
            matched=len(target_rows),
            unmatched=len(error_rows),
            candidates_indexed=len(reference_rows),
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
    def _reference_pk(idcol_id: str | None) -> int | None:
        """The matcher's idcol_id is rilds_reference.idcol_id (as str). To int."""
        if idcol_id is None:
            return None
        try:
            return int(idcol_id)
        except (TypeError, ValueError):
            return None


def _match_attributes(rec: dict[str, Any]) -> dict[str, Any]:
    """Extract the matching-attribute values from a staged record.

    These are denormalized onto target/error rows so each row is
    self-explanatory — you can see the attributes that were used to match (or
    that were present when the match failed) without joining back to stage.
    """
    return {col: rec.get(col) for col in MATCH_ATTRIBUTE_COLUMNS}
