"""The repository interface for the LAND -> STAGE -> TARGET/ERROR model.

A thin, intention-revealing surface — exactly what the pipeline needs — so
alternative backends (Snowflake, DuckDB, in-memory for tests) are cheap to
provide. Concrete implementations own all SQL/driver concerns.

The run lifecycle the orchestrator drives:

    pipeline_run_id = begin_run(...)
    write_land(...)                       # raw cleansed rows, full fidelity
    stage_ids = write_stage(...)          # canonical + blocking cols, PENDING
    members   = load_member_universe()
    ... match in Python ...
    update_stage_matches(...)             # set member_id/score/status in place
    write_target(...) / write_error(...)
    finalize_run(metrics)                 # write the run-log row
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from matchbot.audit.metrics import RunMetrics


class Repository(ABC):
    """Persistence boundary for land/stage/member-universe/target/error/run-log."""

    @abstractmethod
    def init_schema(self) -> None:
        """Create the schema, core tables, and blocking indexes (idempotent)."""

    @abstractmethod
    def begin_run(
        self, *, run_uid: str, provider_code: str, dataset_name: str, runtime: str,
        source_uri: str,
    ) -> int:
        """Insert a pipeline_runs row and return its integer pipeline_run_id."""

    @abstractmethod
    def write_land(
        self,
        *,
        pipeline_run_id: int,
        provider_code: str,
        source_columns: Sequence[str],
        rows: Sequence[Mapping[str, Any]],
    ) -> int:
        """Dump raw file rows verbatim to the per-provider land table.

        LAND is the immutable raw archive: an exact, all-text mirror of the
        incoming file (every source column, values stored as-is).
        """

    @abstractmethod
    def write_stage(
        self, pipeline_run_id: int, rows: Sequence[Mapping[str, Any]]
    ) -> list[int]:
        """Bulk-insert staged rows (status PENDING). Return their stage ids in order."""

    @abstractmethod
    def update_stage_matches(self, updates: Sequence[Mapping[str, Any]]) -> int:
        """Update stage rows in place with member_id/match_score/match_status.

        Each update dict must include ``id`` (the stage id) plus the fields to set.
        """

    @abstractmethod
    def load_member_universe(self) -> list[dict[str, Any]]:
        """Return the authoritative member rows (read-only reference)."""

    @abstractmethod
    def seed_member_universe(
        self, rows: Sequence[Mapping[str, Any]], *, replace: bool = True
    ) -> int:
        """Load member rows (deriving blocking columns). Dev/bootstrap helper."""

    @abstractmethod
    def write_target(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Persist matched rows (stage_id, member_id, score, method)."""

    @abstractmethod
    def write_error(self, rows: Sequence[Mapping[str, Any]]) -> int:
        """Persist unmatched/low-confidence rows (stage_id, decision, reason)."""

    @abstractmethod
    def finalize_run(self, pipeline_run_id: int, metrics: RunMetrics) -> None:
        """Update the pipeline_runs row with final counts/timings/status."""

    @abstractmethod
    def close(self) -> None:
        """Release connections/resources."""

    def __enter__(self) -> Repository:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
