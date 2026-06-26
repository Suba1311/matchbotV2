"""SQLAlchemy table definitions for the LAND -> STAGE -> TARGET/ERROR model.

Parameterized by the env-driven schema (``Settings.db_schema``) — the schema
name appears nowhere as a literal. ``build_metadata(schema)`` returns fresh
MetaData bound to that schema, so switching schemas is purely a config change.

Tables (mirrors the reference architecture and the agreed DDLs):

* ``pipeline_runs``    — one row per run; issues the integer pipeline_run_id and
                         holds the audit/run-log (counts, timings, match rate, DQ).
* ``<provider>_land``  — per-provider raw cleansed rows, full fidelity. Created
                         on demand per provider. ``stage.source_row_id`` -> land.id.
* ``stage``            — shared canonical work table with derived blocking columns
                         and match-output columns; the matcher updates it in place.
* ``member_universe``  — authoritative member master + identical blocking columns.
* ``target``           — matched rows (member_id, score, method).
* ``error``            — unmatched / low-confidence rows for optional review.

Integer SERIAL primary keys throughout. FKs are documented but not declared, to
keep bulk loads fast (a deliberate choice; integrity enforced by the pipeline).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Float,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    String,
    Table,
    Text,
    TextClause,
    func,
    text,
)


# --- column groups ----------------------------------------------------------
def _identity_columns() -> list[Column[Any]]:
    """Core identifiers + derived blocking columns (stage & member share these)."""
    return [
        Column("first_name", String(52)),
        Column("middle_name", String(50)),
        Column("last_name", String(52)),
        Column("birth_date", Date),
        Column("gender", String(10)),
        # derived blocking fields (computed in the cleanse stage)
        Column("first_name_std", String(52)),
        Column("last_name_std", String(52)),
        Column("first_name_metaphone", String(50)),
        Column("last_name_metaphone", String(50)),
        Column("last_name8", String(8)),
        Column("birth_year", SmallInteger),
        Column("birth_month", SmallInteger),
        Column("birth_day", SmallInteger),
        # provider-specific strong identifiers
        Column("sasid", String(10)),
        Column("lasid", String(50)),
    ]


def build_metadata(schema: str) -> MetaData:
    """Return MetaData with the core MatchBot tables bound to ``schema``.

    Per-provider ``land`` tables are created separately via
    :func:`build_land_table` because their names depend on the provider.
    """
    md = MetaData(schema=schema)

    # --- pipeline_runs (audit / run-log; issues pipeline_run_id) ------------
    Table(
        "pipeline_runs",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("run_uid", String(64), unique=True, nullable=False),  # external run-* id
        Column("provider_code", String(20), nullable=False),
        Column("dataset_name", String(100)),
        Column("runtime", String(32), nullable=False),
        Column("source_uri", Text),
        Column("status", String(16), nullable=False),
        Column("duration_seconds", Float),
        Column("match_rate", Float),
        Column("rows_received", Integer),
        Column("rows_cleansed", Integer),
        Column("rows_landed", Integer),
        Column("rows_staged", Integer),
        Column("rows_matched", Integer),
        Column("rows_unmatched", Integer),
        Column("rows_ambiguous", Integer),
        Column("rows_skipped", Integer),
        Column("stage_timings", JSON),
        Column("dq_metrics", JSON),
        Column("error", Text),
        Column("started_at", DateTime(timezone=True), server_default=func.now()),
        Column("finished_at", DateTime(timezone=True)),
    )

    # --- stage (shared canonical work table) --------------------------------
    Table(
        "stage",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("pipeline_run_id", Integer, nullable=False, index=True),
        Column("provider_code", String(20), nullable=False),
        Column("dataset_name", String(100), nullable=False),
        Column("source_row_id", Integer, nullable=False),  # FK -> <provider>_land.id
        *_identity_columns(),
        # match output (filled by the matcher)
        Column("member_id", Integer, index=True),  # FK -> member_universe.id, NULL until matched
        Column("match_score", Numeric(5, 4)),
        Column("match_status", String(20), server_default="PENDING", index=True),
        Column("loaded_at", DateTime(timezone=True), server_default=func.now()),
    )

    # --- member_universe (authoritative master) -----------------------------
    Table(
        "member_universe",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        *_identity_columns(),
        Column("source_provider", String(20)),
        Column("source_dataset", String(100)),
        Column("source_row_id", Integer),
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
        Column("updated_at", DateTime(timezone=True), server_default=func.now()),
    )

    # --- target (matched rows) ----------------------------------------------
    # The matching-attribute columns (*_identity_columns) are denormalized here
    # so each matched row is self-explanatory: you can see the attributes that
    # were used to match without joining back to stage.
    Table(
        "target",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("pipeline_run_id", Integer, nullable=False, index=True),
        Column("stage_id", Integer, nullable=False, index=True),  # FK -> stage.id
        Column("member_id", Integer, nullable=False, index=True),  # FK -> member_universe.id
        Column("match_score", Numeric(5, 4), nullable=False),
        Column("match_method", String(20), nullable=False),  # EXACT_SASID / LEVENSHTEIN / ...
        *_identity_columns(),  # incoming record's matching attributes
        Column("matched_at", DateTime(timezone=True), server_default=func.now()),
        Column("matched_by", String(100), server_default="system"),
    )

    # --- error (unmatched / low-confidence rows for review) -----------------
    # Same matching-attribute columns so a reviewer can see exactly what data
    # the record had when it failed to match.
    Table(
        "error",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("pipeline_run_id", Integer, nullable=False, index=True),
        Column("stage_id", Integer, nullable=False),  # FK -> stage.id
        Column("decision", String(20), nullable=False),  # NO_MATCH / LOW_CONFIDENCE
        Column("match_score", Numeric(5, 4)),
        Column("reason", Text),
        *_identity_columns(),  # incoming record's matching attributes
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
    )

    return md


def land_table_name(provider_code: str) -> str:
    """The per-provider land table name, e.g. 'ride' -> 'ride_land'."""
    return f"{provider_code}_land"


def build_land_table(
    md: MetaData, provider_code: str, source_columns: list[str]
) -> Table:
    """Build (or fetch) the per-provider land table — an exact, all-text mirror
    of the incoming file.

    LAND is the immutable raw archive: one table per provider, every source
    column stored verbatim as text (no coercion, no loss), in source order,
    with lowercased names. Plus ``id`` / ``pipeline_run_id`` / ``source_row_id``
    / ``created_at`` for tracking. Built dynamically from the file's columns, so
    any provider works with zero bespoke DDL.
    """
    table_name = land_table_name(provider_code)
    key = table_name if table_name in md.tables else f"{md.schema}.{table_name}"
    if key in md.tables:
        return md.tables[key]

    # Provenance columns we add; never collide with source columns.
    reserved = {"id", "pipeline_run_id", "source_row_id", "created_at"}
    cols: list[Column[Any]] = [
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("pipeline_run_id", Integer, index=True),
        Column("source_row_id", Integer),
    ]
    for raw in source_columns:
        name = raw.strip().lower()
        if not name or name in reserved:
            continue
        reserved.add(name)
        cols.append(Column(name, Text))  # every source column as raw text
    cols.append(Column("created_at", DateTime(timezone=True), server_default=func.now()))
    return Table(table_name, md, *cols)


def search_path_sql(schema: str) -> TextClause:
    """SQL to set the connection search_path to ``schema`` (belt-and-suspenders)."""
    return text(f'SET search_path TO "{schema}"')


# Performance-critical blocking indexes, as (index_name, table, columns) tuples.
# Built programmatically to keep the DDL readable. Created after table creation.
_BLOCKING_INDEXES: tuple[tuple[str, str, str], ...] = (
    ("idx_member_last_name8", "member_universe", "last_name8"),
    ("idx_member_birth_date", "member_universe", "birth_date"),
    ("idx_member_first_metaphone", "member_universe", "first_name_metaphone"),
    ("idx_member_last_metaphone", "member_universe", "last_name_metaphone"),
    ("idx_member_birth_year", "member_universe", "birth_year"),
    ("idx_member_sasid", "member_universe", "sasid"),
    # composite blocking indexes (most important for performance)
    ("idx_member_block_last8_dob", "member_universe", "last_name8, birth_date"),
    ("idx_member_block_meta_year", "member_universe", "last_name_metaphone, birth_year"),
    ("idx_member_block_meta_month", "member_universe", "first_name_metaphone, birth_date"),
    # stage blocking indexes
    ("idx_stage_last_name8", "stage", "last_name8"),
    ("idx_stage_birth_date", "stage", "birth_date"),
    ("idx_stage_first_metaphone", "stage", "first_name_metaphone"),
    ("idx_stage_last_metaphone", "stage", "last_name_metaphone"),
    ("idx_stage_sasid", "stage", "sasid"),
)


def extra_index_sql(schema: str) -> list[TextClause]:
    """DDL for the performance-critical blocking indexes from the agreed design."""
    return [
        text(f'CREATE INDEX IF NOT EXISTS {name} ON "{schema}".{table}({cols})')
        for name, table, cols in _BLOCKING_INDEXES
    ]
