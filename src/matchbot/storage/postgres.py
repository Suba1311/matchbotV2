"""Postgres implementation of the LAND -> STAGE -> TARGET/ERROR repository.

SQLAlchemy Core over psycopg 3. The schema name comes from ``Settings.db_schema``
only; every table is schema-qualified via the MetaData and the connection pins
``search_path``. ``init_schema`` creates the schema, core tables, and blocking
indexes idempotently. Per-provider land tables are created on first use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    bindparam,
    create_engine,
    insert,
    select,
    text,
    update,
)
from sqlalchemy import (
    schema as sa_schema,
)
from tenacity import retry, stop_after_attempt, wait_exponential

from matchbot.logging_setup import get_logger
from matchbot.matching.derive import add_derived_columns
from matchbot.storage.base import Repository
from matchbot.storage.schema import (
    build_land_table,
    build_metadata,
    extra_index_sql,
    search_path_sql,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from matchbot.audit.metrics import RunMetrics
    from matchbot.config.models import StandardizationConfig
    from matchbot.config.settings import Settings

log = get_logger(__name__)


def _normalize_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


class PostgresRepository(Repository):
    """Schema-from-env Postgres repository for the full pipeline model."""

    def __init__(self, settings: Settings) -> None:
        self._schema = settings.db_schema
        self._md = build_metadata(self._schema)
        self._std_config: StandardizationConfig | None = None  # lazy, for member seed
        self._settings = settings
        self._engine = create_engine(
            _normalize_url(settings.database_url), pool_pre_ping=True, future=True
        )
        mu_url = settings.effective_member_universe_url
        self._mu_engine = (
            self._engine
            if mu_url == settings.database_url
            else create_engine(_normalize_url(mu_url), pool_pre_ping=True, future=True)
        )
        self._t = {t.name: t for t in self._md.tables.values()}

    # --- lifecycle ----------------------------------------------------------
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.5, max=5))
    def init_schema(self) -> None:
        with self._engine.begin() as conn:
            conn.execute(sa_schema.CreateSchema(self._schema, if_not_exists=True))
            conn.execute(search_path_sql(self._schema))
        self._md.create_all(self._engine)
        with self._engine.begin() as conn:
            conn.execute(search_path_sql(self._schema))
            for stmt in extra_index_sql(self._schema):
                conn.execute(stmt)
        log.info("schema.initialized", schema=self._schema)

    def close(self) -> None:
        self._engine.dispose()
        if self._mu_engine is not self._engine:
            self._mu_engine.dispose()

    # --- run lifecycle ------------------------------------------------------
    def begin_run(
        self, *, run_uid: str, provider_code: str, dataset_name: str, runtime: str,
        source_uri: str,
    ) -> int:
        table = self._t["pipeline_runs"]
        with self._engine.begin() as conn:
            conn.execute(search_path_sql(self._schema))
            result = conn.execute(
                insert(table)
                .values(
                    run_uid=run_uid,
                    provider_code=provider_code,
                    dataset_name=dataset_name,
                    runtime=runtime,
                    source_uri=source_uri,
                    status="RUNNING",
                )
                .returning(table.c.id)
            )
            run_id = int(result.scalar_one())
        log.info("run.created", pipeline_run_id=run_id, run_uid=run_uid)
        return run_id

    def finalize_run(self, pipeline_run_id: int, metrics: RunMetrics) -> None:
        table = self._t["pipeline_runs"]
        m = metrics.to_dict()
        with self._engine.begin() as conn:
            conn.execute(search_path_sql(self._schema))
            conn.execute(
                update(table)
                .where(table.c.id == pipeline_run_id)
                .values(
                    status=m["status"].upper(),
                    duration_seconds=m["duration_seconds"],
                    match_rate=m["match_rate"],
                    rows_received=m["rows_received"],
                    rows_cleansed=m["rows_cleansed"],
                    rows_landed=m["rows_landed"],
                    rows_staged=m["rows_staged"],
                    rows_matched=m["rows_matched"],
                    rows_unmatched=m["rows_unmatched"],
                    rows_ambiguous=m["rows_ambiguous"],
                    rows_skipped=m["rows_skipped"],
                    stage_timings=m["stage_timings"],
                    dq_metrics=m["dq_metrics"],
                    error=m["error"],
                    finished_at=text("NOW()"),
                )
            )
        log.info("run.finalized", pipeline_run_id=pipeline_run_id, match_rate=m["match_rate"])

    # --- land ---------------------------------------------------------------
    def write_land(
        self, *, pipeline_run_id: int, provider_code: str,
        source_columns: Sequence[str], rows: Sequence[Mapping[str, Any]],
    ) -> int:
        """Dump the raw file rows verbatim into the per-provider land table.

        ``source_columns`` are the file's columns (used to build/extend the
        all-text land table). Values are stored as-is (no coercion).
        """
        if not rows:
            return 0
        land = build_land_table(self._md, provider_code, list(source_columns))
        land.create(self._engine, checkfirst=True)
        valid = set(land.columns.keys())
        cleaned = []
        for r in rows:
            # Lowercase source keys to match the land columns; keep values raw.
            row = {k.strip().lower(): v for k, v in r.items() if k.strip().lower() in valid}
            row["pipeline_run_id"] = pipeline_run_id
            if "source_row_id" in r:
                row["source_row_id"] = r["source_row_id"]
            cleaned.append(row)
        with self._engine.begin() as conn:
            conn.execute(search_path_sql(self._schema))
            conn.execute(insert(land), cleaned)
        log.info("land.written", table=land.name, count=len(cleaned))
        return len(cleaned)

    # --- stage --------------------------------------------------------------
    def write_stage(
        self, pipeline_run_id: int, rows: Sequence[Mapping[str, Any]]
    ) -> list[int]:
        if not rows:
            return []
        table = self._t["stage"]
        valid = set(table.columns.keys())
        cleaned = [
            {**{k: v for k, v in r.items() if k in valid}, "pipeline_run_id": pipeline_run_id}
            for r in rows
        ]
        with self._engine.begin() as conn:
            conn.execute(search_path_sql(self._schema))
            result = conn.execute(insert(table).returning(table.c.id), cleaned)
            ids = [int(row[0]) for row in result.fetchall()]
        log.info("stage.written", count=len(ids))
        return ids

    def update_stage_matches(self, updates: Sequence[Mapping[str, Any]]) -> int:
        if not updates:
            return 0
        table = self._t["stage"]
        # 'id' is reserved as a bind name in UPDATE; bind the key on 'b_id'.
        params = [
            {
                "b_id": u["id"],
                "member_id": u.get("member_id"),
                "match_score": u.get("match_score"),
                "match_status": u.get("match_status"),
            }
            for u in updates
        ]
        stmt = (
            update(table)
            .where(table.c.id == bindparam("b_id"))
            .values(
                member_id=bindparam("member_id"),
                match_score=bindparam("match_score"),
                match_status=bindparam("match_status"),
            )
        )
        with self._engine.begin() as conn:
            conn.execute(search_path_sql(self._schema))
            conn.execute(stmt, params)
        log.info("stage.matches_updated", count=len(params))
        return len(params)

    # --- member universe ----------------------------------------------------
    def load_member_universe(self) -> list[dict[str, Any]]:
        table = self._t["member_universe"]
        with self._mu_engine.connect() as conn:
            conn.execute(search_path_sql(self._schema))
            rows = conn.execute(select(table)).mappings().all()
        members = []
        for r in rows:
            d = self._coerce(dict(r))
            # Expose the generic canonical id the matchers key on, sourced from
            # the provider-specific sasid/lasid column.
            d["member_external_id"] = d.get("sasid") or d.get("lasid")
            members.append(d)
        log.info("member_universe.loaded", count=len(members))
        return members

    def seed_member_universe(
        self, rows: Sequence[Mapping[str, Any]], *, replace: bool = True
    ) -> int:
        if not rows:
            return 0
        # Derive blocking columns so members and stage share identical fields.
        import polars as pl

        df = add_derived_columns(pl.DataFrame(list(rows), infer_schema_length=None),
                                 self._standardization())
        table = self._t["member_universe"]
        valid = set(table.columns.keys())
        cleaned = []
        for r in df.to_dicts():
            row = {k: v for k, v in r.items() if k in valid}
            # Map the generic external id onto the provider-specific sasid column.
            if r.get("member_external_id") is not None and not row.get("sasid"):
                row["sasid"] = r["member_external_id"]
            cleaned.append(row)
        with self._engine.begin() as conn:
            conn.execute(search_path_sql(self._schema))
            if replace:
                conn.execute(
                    text(
                        f'TRUNCATE TABLE "{self._schema}".member_universe '
                        "RESTART IDENTITY CASCADE"
                    )
                )
            conn.execute(insert(table), cleaned)
        log.info("member_universe.seeded", count=len(cleaned), replaced=replace)
        return len(cleaned)

    # --- target / error -----------------------------------------------------
    def write_target(self, rows: Sequence[Mapping[str, Any]]) -> int:
        return self._bulk_insert("target", rows)

    def write_error(self, rows: Sequence[Mapping[str, Any]]) -> int:
        return self._bulk_insert("error", rows)

    # --- helpers ------------------------------------------------------------
    def _bulk_insert(self, table_name: str, rows: Sequence[Mapping[str, Any]]) -> int:
        if not rows:
            return 0
        table = self._t[table_name]
        valid = set(table.columns.keys())
        cleaned = [{k: v for k, v in r.items() if k in valid} for r in rows]
        with self._engine.begin() as conn:
            conn.execute(search_path_sql(self._schema))
            conn.execute(insert(table), cleaned)
        log.info("rows.written", table=table_name, count=len(cleaned))
        return len(cleaned)

    def _standardization(self) -> StandardizationConfig:
        """Load standardization config once for member-seed derivation."""
        if self._std_config is None:
            from matchbot.config.loader import load_config

            self._std_config = load_config(
                self._settings.config_dir
            ).global_config.standardization
        return self._std_config

    @staticmethod
    def _coerce(row: dict[str, Any]) -> dict[str, Any]:
        """Render dates as ISO strings for uniform comparison with staged data."""
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in row.items()}


def make_repository(settings: Settings) -> Repository:
    """Factory: return the repository for the configured backend (Postgres today)."""
    return PostgresRepository(settings)
