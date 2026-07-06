"""SQLAlchemy table definitions for the LAND -> STAGE -> TARGET/ERROR model.

Parameterized by the env-driven schema (``Settings.db_schema``) — the schema
name appears nowhere as a literal. ``build_metadata(schema)`` returns fresh
MetaData bound to that schema, so switching schemas is purely a config change.

Tables (mirrors the reference architecture and the agreed DDLs):

* ``rilds_audit``      — one row per run; issues the integer pipeline_run_id and
                         holds the audit/run-log (counts, timings, match rate, DQ).
                         (Formerly ``pipeline_runs``.)
* ``<provider>_land``  — per-provider raw cleansed rows, full fidelity. Created
                         on demand per provider. ``rilds_stage.source_row_id`` -> land.id.
* ``rilds_land_rejects`` — raw lines ParseStage couldn't cleanly parse (field-
                         count mismatch, usually an unescaped comma in the
                         source file), kept verbatim for DQ investigation.
* ``rilds_stage``      — shared canonical work table with derived blocking columns
                         and match-output columns; the matcher updates it in place.
                         (Formerly ``stage``.)
* ``rilds_reference``  — the matching source: person + address reference (30
                         provider IDs + derived identifiers), loaded externally
                         from proddb (not by this pipeline) — see
                         person_pii_reference_temp_tables.md for provenance.
                         ``idcol_id`` is its natural primary key.
* ``member_universe``  — legacy member master + identical blocking columns.
                         No longer the active matching source (superseded by
                         ``rilds_reference``); kept, unused, for now.
* ``rilds_matched``    — matched rows (idcol_id, score, method). (Formerly
                         ``target``; ``member_id`` renamed to ``idcol_id`` since
                         it now references ``rilds_reference.idcol_id``.)
* ``rilds_error``      — unmatched / low-confidence rows for optional review.
                         (Formerly ``error``.)

Integer SERIAL primary keys throughout except ``rilds_reference.idcol_id``,
which is a natural key (proddb's ``identifiers_idcollection.id``) rather than
an autoincrement surrogate. FKs are documented but not declared, to keep bulk
loads fast (a deliberate choice; integrity enforced by the pipeline).
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
        # derived blocking fields (computed in the cleanse stage). Named
        # *_metaphone1 (not *_metaphone) to match rilds_reference's naming,
        # which is now the source of truth for this attribute name.
        Column("first_name_std", String(52)),
        Column("last_name_std", String(52)),
        Column("first_name_metaphone1", String(50)),
        Column("last_name_metaphone1", String(50)),
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

    # --- rilds_audit (audit / run-log; issues pipeline_run_id) --------------
    Table(
        "rilds_audit",
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
        Column("rows_rejected", Integer),
        Column("rows_cleansed", Integer),
        Column("rows_landed", Integer),
        Column("rows_staged", Integer),
        Column("rows_matched", Integer),
        Column("rows_unmatched", Integer),
        Column("rows_skipped", Integer),
        Column("stage_timings", JSON),
        Column("dq_metrics", JSON),
        Column("error", Text),
        Column("started_at", DateTime(timezone=True), server_default=func.now()),
        Column("finished_at", DateTime(timezone=True)),
    )

    # --- rilds_land_rejects (raw lines ParseStage couldn't cleanly parse) ---
    # Real source extracts contain rows with unescaped commas inside unquoted
    # fields (e.g. a high-school name), which shifts every column after the
    # extra comma — Polars' truncate_ragged_lines=True does NOT reject these,
    # it silently drops trailing fields and lets the row through *shifted*,
    # so e.g. a gender column ends up holding a district name. ParseStage now
    # pre-validates field counts itself (before Polars ever sees the bytes),
    # routes anything that doesn't match the header's field count here as the
    # verbatim original line, and only hands clean rows to Polars.
    Table(
        "rilds_land_rejects",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("pipeline_run_id", Integer, nullable=False, index=True),
        Column("provider_code", String(20), nullable=False),
        Column("raw_line", Text, nullable=False),
        Column("reason", Text, nullable=False),
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
    )

    # --- rilds_stage (shared canonical work table) --------------------------
    Table(
        "rilds_stage",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("pipeline_run_id", Integer, nullable=False, index=True),
        Column("provider_code", String(20), nullable=False),
        Column("dataset_name", String(100), nullable=False),
        Column("source_row_id", Integer, nullable=False),  # FK -> <provider>_land.id
        *_identity_columns(),
        # match output (filled by the matcher). idcol_id references
        # rilds_reference.idcol_id (not member_universe.id — renamed when
        # rilds_reference became the matching source).
        Column("idcol_id", Integer, index=True),  # FK -> rilds_reference.idcol_id, NULL until matched
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

    # --- rilds_matched (matched rows) ---------------------------------------
    # The matching-attribute columns (*_identity_columns) are denormalized here
    # so each matched row is self-explanatory: you can see the attributes that
    # were used to match without joining back to stage.
    Table(
        "rilds_matched",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("pipeline_run_id", Integer, nullable=False, index=True),
        Column("stage_id", Integer, nullable=False, index=True),  # FK -> rilds_stage.id
        Column("idcol_id", Integer, nullable=False, index=True),  # FK -> rilds_reference.idcol_id
        Column("match_score", Numeric(5, 4), nullable=False),
        Column("match_method", String(20), nullable=False),  # EXACT_SASID / LEVENSHTEIN / ...
        *_identity_columns(),  # incoming record's matching attributes
        Column("matched_at", DateTime(timezone=True), server_default=func.now()),
        Column("matched_by", String(100), server_default="system"),
    )

    # --- rilds_error (unmatched / low-confidence rows for review) -----------
    # Same matching-attribute columns so a reviewer can see exactly what data
    # the record had when it failed to match.
    Table(
        "rilds_error",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("pipeline_run_id", Integer, nullable=False, index=True),
        Column("stage_id", Integer, nullable=False),  # FK -> rilds_stage.id
        Column("decision", String(20), nullable=False),  # NO_MATCH / LOW_CONFIDENCE
        Column("match_score", Numeric(5, 4)),
        Column("reason", Text),
        *_identity_columns(),  # incoming record's matching attributes
        Column("created_at", DateTime(timezone=True), server_default=func.now()),
    )

    # --- rilds_reference (future matching source; not yet wired in) ---------
    # A richer person-keyed identity + address reference, intended to
    # eventually replace member_universe as what the matcher chain compares
    # against — 30 provider IDs vs. today's sasid/lasid only. Mirrors proddb's
    # identifiers_idcollection (+ per-source address union); loaded externally
    # via a one-off query, not by this pipeline — see
    # person_pii_reference_temp_tables.md for provenance. Table only for now:
    # load_member_universe()/seed_member_universe(), blocking, and the matcher
    # chain still read/write member_universe until that cutover happens.
    # idcol_id is identifiers_idcollection.id in proddb; treated as this
    # table's PK on the understanding that the load process guarantees one
    # row per idcol_id.
    Table(
        "rilds_reference",
        md,
        Column("idcol_id", Integer, primary_key=True),
        Column("person_id", Integer, index=True),  # nullable: NULL until proddb second-pass linkage
        Column("dataset_id", Integer, index=True),

        # CoreIdentifiers
        Column("first_name", String(52)),
        Column("middle_name", String(50)),
        Column("last_name", String(52)),
        Column("birth_date", Date),
        Column("gender", String(10)),
        Column("ssn", String(11)),

        # ModelIdentifiers — provider-issued ids, all strings (some source
        # columns are integer-typed upstream, e.g. zip5; cast to text on load).
        Column("apprentice_id", String(50)),
        Column("brown_id", String(50)),
        Column("bryant_id", String(50)),
        Column("ccri_id", String(50)),
        Column("college_board_id", String(50)),
        Column("dcyf_id", String(50)),
        Column("dlt_ern", String(50)),
        Column("employri_id", String(50)),
        Column("ged_id", String(50)),
        Column("jwu_id", String(50)),
        Column("kidsnet_child_id", String(50)),
        Column("laces_id", String(50)),
        Column("laces_staff_id", String(50)),
        Column("laces_student_id", String(50)),
        Column("lasid", String(50)),
        Column("nspid", String(50)),
        Column("ods", String(50)),
        Column("ric_id", String(50)),
        Column("ride_cert_id", String(50)),
        Column("ridoh_lead_id", String(50)),
        Column("risd_id", String(50)),
        Column("rjri_id", String(50)),
        Column("rwu_id", String(50)),
        Column("salve_id", String(50)),
        Column("sasid", String(10)),
        Column("uri_id", String(50)),
        Column("voter_id", String(50)),
        Column("workforce_id", String(50)),
        Column("providencecollege_id", String(50)),
        Column("netech_id", String(50)),

        # DerivedIdentifiers — computed in proddb at idcol creation, not by
        # this pipeline; stored verbatim.
        Column("first_name_std", String(52)),
        Column("first_name_metaphone1", String(50)),
        Column("first_name_metaphone2", String(50)),
        Column("first_name_transposed", String(52)),
        Column("first_initial", String(1)),
        Column("middle_name_std", String(50)),
        Column("middle_initial", String(1)),
        Column("last_name_std", String(52)),
        Column("last_name_metaphone1", String(50)),
        Column("last_name_metaphone2", String(50)),
        Column("last_name_transposed", String(52)),
        Column("last_name_suffix", String(10)),
        Column("last_initial", String(1)),
        Column("last_name8", String(8)),
        Column("full_name_std", String(150)),
        Column("full_name_metaphone", String(100)),
        Column("full_name_transposed", String(150)),
        Column("full_name_dob", String(160)),
        Column("birth_month", SmallInteger),
        Column("birth_day", SmallInteger),
        Column("birth_year", SmallInteger),
        Column("ssn4", String(4)),

        # Address (one row per idcol_id in this table; see the source doc for
        # how multi-address persons were resolved to a single row on load).
        Column("address_source", String(100)),  # originating source table name
        Column("address1", String(200)),
        Column("address2", String(200)),
        Column("city", String(100)),
        Column("state", String(20)),
        Column("zip", String(20)),
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
    # member_universe indexes — table is unused (superseded by
    # rilds_reference) but kept fully functional/indexed regardless.
    ("idx_member_last_name8", "member_universe", "last_name8"),
    ("idx_member_birth_date", "member_universe", "birth_date"),
    ("idx_member_first_metaphone", "member_universe", "first_name_metaphone1"),
    ("idx_member_last_metaphone", "member_universe", "last_name_metaphone1"),
    ("idx_member_birth_year", "member_universe", "birth_year"),
    ("idx_member_sasid", "member_universe", "sasid"),
    # composite blocking indexes (most important for performance)
    ("idx_member_block_last8_dob", "member_universe", "last_name8, birth_date"),
    ("idx_member_block_meta_year", "member_universe", "last_name_metaphone1, birth_year"),
    ("idx_member_block_meta_month", "member_universe", "first_name_metaphone1, birth_date"),
    # stage blocking indexes
    ("idx_stage_last_name8", "rilds_stage", "last_name8"),
    ("idx_stage_birth_date", "rilds_stage", "birth_date"),
    ("idx_stage_first_metaphone", "rilds_stage", "first_name_metaphone1"),
    ("idx_stage_last_metaphone", "rilds_stage", "last_name_metaphone1"),
    ("idx_stage_sasid", "rilds_stage", "sasid"),
    # rilds_reference blocking indexes — the active matching source.
    ("idx_reference_last_name8", "rilds_reference", "last_name8"),
    ("idx_reference_birth_date", "rilds_reference", "birth_date"),
    ("idx_reference_first_metaphone", "rilds_reference", "first_name_metaphone1"),
    ("idx_reference_last_metaphone", "rilds_reference", "last_name_metaphone1"),
    ("idx_reference_birth_year", "rilds_reference", "birth_year"),
    ("idx_reference_sasid", "rilds_reference", "sasid"),
    ("idx_reference_block_last8_dob", "rilds_reference", "last_name8, birth_date"),
    ("idx_reference_block_meta_year", "rilds_reference", "last_name_metaphone1, birth_year"),
    ("idx_reference_block_meta_month", "rilds_reference", "first_name_metaphone1, birth_date"),
)


def extra_index_sql(schema: str) -> list[TextClause]:
    """DDL for the performance-critical blocking indexes from the agreed design."""
    return [
        text(f'CREATE INDEX IF NOT EXISTS {name} ON "{schema}".{table}({cols})')
        for name, table, cols in _BLOCKING_INDEXES
    ]
