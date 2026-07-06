"""Derived blocking-field computation, shared by STAGE and MEMBER_UNIVERSE.

Both the cleanse stage (for incoming records) and the member-universe seed path
must compute the *same* derived columns so blocking keys line up. Centralizing
that here guarantees they never drift.

Derived columns (mirror the agreed DDL):
    first_name_std, last_name_std        — uppercased, stripped, suffix-removed
    first_name_metaphone1, last_name_metaphone1 — primary metaphone code
    last_name8                           — first 8 chars of last_name_std
    birth_year, birth_month, birth_day   — decomposed from birth_date

Implemented with Polars expressions where possible; metaphone (a Python
function) is applied via map_elements over the standardized name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import polars as pl

from matchbot.matching import standardize as S

if TYPE_CHECKING:
    from matchbot.config.models import StandardizationConfig

# The derived columns this module guarantees to produce.
DERIVED_COLUMNS: tuple[str, ...] = (
    "first_name_std",
    "last_name_std",
    "first_name_metaphone1",
    "last_name_metaphone1",
    "last_name8",
    "birth_year",
    "birth_month",
    "birth_day",
)


def add_derived_columns(df: pl.DataFrame, std_config: StandardizationConfig) -> pl.DataFrame:
    """Return ``df`` with all derived blocking columns added (idempotent).

    Missing source columns are tolerated: derived values become null. This makes
    the function safe for providers lacking, e.g., birth_date (like RIDE).
    """
    out = df

    # Standardized names (suffix/prefix-stripped, uppercased, alnum-only).
    if "first_name" in out.columns:
        out = out.with_columns(
            pl.col("first_name")
            .map_elements(lambda v: S.std_name(v, std_config), return_dtype=pl.Utf8)
            .alias("first_name_std")
        )
    else:
        out = out.with_columns(pl.lit(None, dtype=pl.Utf8).alias("first_name_std"))

    if "last_name" in out.columns:
        out = out.with_columns(
            pl.col("last_name")
            .map_elements(lambda v: S.std_name(v, std_config), return_dtype=pl.Utf8)
            .alias("last_name_std")
        )
    else:
        out = out.with_columns(pl.lit(None, dtype=pl.Utf8).alias("last_name_std"))

    # Metaphone codes (primary) off the standardized names.
    out = out.with_columns(
        pl.col("first_name_std")
        .map_elements(S.metaphone, return_dtype=pl.Utf8)
        .alias("first_name_metaphone1"),
        pl.col("last_name_std")
        .map_elements(S.metaphone, return_dtype=pl.Utf8)
        .alias("last_name_metaphone1"),
    )

    # last_name8 — first 8 chars of the standardized last name.
    out = out.with_columns(
        pl.col("last_name_std").str.slice(0, 8).alias("last_name8")
    )

    # Decompose birth_date (stored as ISO string or Date) into y/m/d.
    if "birth_date" in out.columns:
        bd = pl.col("birth_date").cast(pl.Utf8, strict=False).str.strptime(
            pl.Date, format="%Y-%m-%d", strict=False
        )
        out = out.with_columns(
            bd.dt.year().cast(pl.Int16).alias("birth_year"),
            bd.dt.month().cast(pl.Int16).alias("birth_month"),
            bd.dt.day().cast(pl.Int16).alias("birth_day"),
        )
    else:
        out = out.with_columns(
            pl.lit(None, dtype=pl.Int16).alias("birth_year"),
            pl.lit(None, dtype=pl.Int16).alias("birth_month"),
            pl.lit(None, dtype=pl.Int16).alias("birth_day"),
        )

    return out
