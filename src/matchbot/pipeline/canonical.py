"""Stage 3 — Map to Canonical.

Renames the provider's raw columns to canonical attribute names per the
provider's ``column_mappings``, drops unmapped columns (except provenance), and
ensures every canonical attribute exists (missing ones become null). The output
is the STAGE frame: a uniform canonical shape regardless of source provider.

Note on ordering: in this pipeline the cleanse stage runs *before* canonical
mapping conceptually, but both operate on mapped names. To keep each stage
single-purpose, ParseStage emits raw columns, this stage renames to canonical,
and CleanseStage (which depends only on canonical names) runs after. The
orchestrator wires the order; stages stay independent.
"""

from __future__ import annotations

import polars as pl

from matchbot.domain.canonical import CANONICAL_NAMES, PROVENANCE_COLUMNS
from matchbot.domain.enums import Stage
from matchbot.logging_setup import get_logger
from matchbot.pipeline.base import PipelineContext, StageResult

log = get_logger(__name__)


class CanonicalStage:
    """Rename raw provider columns onto the canonical schema."""

    stage = Stage.CANONICAL

    def run(self, ctx: PipelineContext, frame: pl.DataFrame) -> StageResult:
        mapping = ctx.provider.column_mappings  # raw -> canonical
        present = set(frame.columns)

        rename = {raw: canon for raw, canon in mapping.items() if raw in present}
        missing_raw = [raw for raw in mapping if raw not in present]
        if missing_raw:
            log.warning(
                "canonical.missing_source_columns",
                provider=ctx.provider.provider_id,
                missing=missing_raw,
            )

        df = frame.rename(rename)

        # Keep only canonical attributes + provenance.
        keep = [c for c in df.columns if c in CANONICAL_NAMES or c in PROVENANCE_COLUMNS]
        df = df.select(keep)

        # Ensure every canonical attribute column exists (null if absent).
        for attr in CANONICAL_NAMES:
            if attr not in df.columns:
                df = df.with_columns(pl.lit(None).cast(pl.Utf8).alias(attr))

        log.info("canonical.done", rows=df.height, attributes=len(CANONICAL_NAMES))
        return StageResult(frame=df)
