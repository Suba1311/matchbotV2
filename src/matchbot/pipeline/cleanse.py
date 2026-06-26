"""Stage 2 — Cleanse & Data Quality.

Operates on canonical column names. Three jobs:

1. **Transform** each attribute per the provider's ``transforms`` (trim, strip
   substrings, uppercase, zero-pad, date parse) using Polars expressions —
   vectorized, not row-by-row.
2. **Standardize** values that the legacy system hardcoded: gender via the
   configured map, and derived standardized name/ssn columns used by matching.
3. **Data quality**: drop rows missing any ``skip_if_null`` attribute (counted),
   and evaluate global DQ rules into run metrics.

Runs after CanonicalStage so it can rely on canonical names existing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from matchbot.domain.enums import Stage
from matchbot.logging_setup import get_logger
from matchbot.matching import standardize as S
from matchbot.matching.derive import add_derived_columns
from matchbot.pipeline.base import PipelineContext, StageResult

if TYPE_CHECKING:
    from matchbot.config.models import DQRule, StandardizationConfig, TransformSpec

log = get_logger(__name__)


def _apply_string_transforms(col: str, spec: TransformSpec) -> pl.Expr:
    """Build a Polars expression applying string transforms for one column."""
    e = pl.col(col).cast(pl.Utf8, strict=False)
    if spec.trim:
        e = e.str.strip_chars()
    for sub in spec.strip:
        e = e.str.replace_all(sub, "", literal=True)
    if spec.upper:
        e = e.str.to_uppercase()
    if spec.zero_pad:
        e = e.str.zfill(spec.zero_pad)
    # Empty string -> null for consistent downstream null semantics.
    return pl.when(e.str.len_chars() == 0).then(None).otherwise(e).alias(col)


def _parse_date_series(s: pl.Series, fmt: str) -> pl.Series:
    """Parse a Utf8 series to ISO date strings using strptime ``fmt``."""
    parsed = s.str.strptime(pl.Date, format=fmt, strict=False)
    return parsed.dt.to_string("%Y-%m-%d")


class CleanseStage:
    """Transform + standardize + DQ. Records DQ metrics and skip counts."""

    stage = Stage.CLEANSE

    def run(self, ctx: PipelineContext, frame: pl.DataFrame) -> StageResult:
        provider = ctx.provider
        std = ctx.config.global_config.standardization
        df = frame
        rows_in = df.height

        # 1. Per-attribute transforms.
        for attr, spec in provider.transforms.items():
            if attr not in df.columns:
                continue
            if spec.type == "date" and spec.format:
                parsed = _parse_date_series(df[attr].cast(pl.Utf8, strict=False), spec.format)
                df = df.with_columns(parsed.alias(attr))
            else:
                df = df.with_columns(_apply_string_transforms(attr, spec))

        # Default trim/empty->null for any string attribute lacking an explicit
        # transform, so matching sees clean values.
        for attr in df.columns:
            if attr in provider.transforms:
                continue
            if df[attr].dtype == pl.Utf8:
                e = pl.col(attr).str.strip_chars()
                df = df.with_columns(
                    pl.when(e.str.len_chars() == 0).then(None).otherwise(e).alias(attr)
                )

        # 2. Standardization (gender map; derived std name/ssn columns).
        if "gender" in df.columns:
            df = df.with_columns(
                pl.col("gender").map_elements(lambda v: S.std_gender(v, std), return_dtype=pl.Utf8)
            )
        df = self._add_derived(df, std)

        # Populate the provider-specific `sasid` column from the canonical
        # `member_external_id` so stage, matching, and the denormalized
        # target/error attributes all carry it consistently.
        if "member_external_id" in df.columns and "sasid" not in df.columns:
            df = df.with_columns(pl.col("member_external_id").alias("sasid"))

        # 3. skip_if_null DQ gate.
        skipped = 0
        if provider.skip_if_null:
            cols = [c for c in provider.skip_if_null if c in df.columns]
            if cols:
                before = df.height
                predicate = pl.all_horizontal([pl.col(c).is_not_null() for c in cols])
                df = df.filter(predicate)
                skipped = before - df.height

        ctx.metrics.rows_skipped += skipped

        # 4. Global DQ rules -> metrics (non-blocking).
        self._evaluate_dq(ctx, df)

        log.info("cleanse.done", rows_in=rows_in, rows_out=df.height, skipped=skipped)
        return StageResult(frame=df)

    def _add_derived(self, df: pl.DataFrame, std_config: StandardizationConfig) -> pl.DataFrame:
        """Add all derived blocking columns (std names, metaphone, last_name8, y/m/d).

        Delegates to the shared deriver so STAGE and MEMBER_UNIVERSE compute the
        same fields and blocking keys always line up.
        """
        # Normalize the raw name columns first (uppercase/trim) so downstream
        # exact comparisons and the std/metaphone derivations are consistent.
        norm: list[pl.Expr] = []
        if "first_name" in df.columns:
            norm.append(pl.col("first_name").str.strip_chars().str.to_uppercase().alias("first_name"))
        if "last_name" in df.columns:
            norm.append(pl.col("last_name").str.strip_chars().str.to_uppercase().alias("last_name"))
        if norm:
            df = df.with_columns(norm)
        return add_derived_columns(df, std_config)

    def _evaluate_dq(self, ctx: PipelineContext, df: pl.DataFrame) -> None:
        """Evaluate configured DQ rules; store pass-rate metrics.

        Empty frames (e.g. every row skipped) record 0 failures / 1.0 pass-rate
        rather than erroring — there is simply nothing to fail.
        """
        height = df.height
        total = height or 1
        for rule in ctx.config.global_config.dq_rules:
            attr = rule.attribute
            if attr not in df.columns:
                continue
            passes = 0 if height == 0 else self._count_passes(rule, df[attr], height)
            ctx.metrics.dq_metrics[rule.name] = {
                "attribute": attr,
                "rule": rule.rule,
                "severity": rule.severity,
                "pass_rate": round(passes / total, 4) if height else 1.0,
                "failures": int(height - passes),
            }

    @staticmethod
    def _count_passes(rule: DQRule, col: pl.Series, height: int) -> int:
        """Number of rows passing one DQ rule on a non-empty column."""
        if rule.rule == "not_null":
            return int(col.is_not_null().sum())
        if rule.rule == "regex" and rule.pattern:
            return int(col.cast(pl.Utf8, strict=False).str.contains(rule.pattern).sum() or 0)
        if rule.rule == "length":
            lengths = col.cast(pl.Utf8, strict=False).str.len_chars()
            ok = pl.repeat(True, n=height, eager=True)
            if rule.min_length is not None:
                ok = ok & (lengths >= rule.min_length).fill_null(value=False)
            if rule.max_length is not None:
                ok = ok & (lengths <= rule.max_length).fill_null(value=False)
            return int(ok.sum())
        if rule.rule == "in_set" and rule.allowed:
            return int(col.is_in(rule.allowed).sum())
        return height  # unknown rule -> treat as all-pass (no-op)
