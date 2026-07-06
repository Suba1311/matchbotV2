"""The per-run metrics accumulator.

Deliberately free of any DB or cloud dependency: it is a plain in-memory object
the orchestrator fills in, then hands to the repository for persistence and to
the logger for the summary line. The metrics requested for benchmarking the
three runtimes — wall-clock time, per-stage timings, and match rate — are all
first-class here, alongside per-hop row counts and DQ metrics.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from matchbot.domain.enums import RunStatus, Stage


@dataclass(slots=True)
class StageTiming:
    """Wall-clock timing and row delta for a single stage."""

    stage: str
    seconds: float
    rows_in: int
    rows_out: int


@dataclass(slots=True)
class RunMetrics:
    """Mutable accumulator for one pipeline run.

    Row counts use the architecture's hop vocabulary: received, cleansed,
    landed, staged, matched, unmatched.
    """

    run_id: str
    provider_id: str
    runtime: str
    source_uri: str

    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    status: RunStatus = RunStatus.SUCCESS

    # Per-hop row counts. rows_received is the total lines read from the
    # source file (clean + rejected); rows_rejected is the parse-stage subset
    # of those that had a field-count mismatch (kept verbatim in
    # rilds_land_rejects) — distinct from rows_skipped, which is cleanse-stage
    # skip_if_null drops (well-formed rows missing a required value).
    rows_received: int = 0
    rows_rejected: int = 0
    rows_cleansed: int = 0
    rows_landed: int = 0
    rows_staged: int = 0
    rows_matched: int = 0
    rows_unmatched: int = 0
    rows_skipped: int = 0

    # Per-stage timings, and free-form DQ metrics keyed by rule name.
    stage_timings: list[StageTiming] = field(default_factory=list)
    dq_metrics: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    # Human-readable attribute names the matcher chain actually compared
    # (e.g. ["SASID"], or ["First Name", "Last Name", "Birth Date", "SSN"]).
    # Set once by the orchestrator from the resolved matcher chain; purely
    # informational — for notifications/reporting, not matching logic.
    matched_on: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at if self.finished_at is not None else time.time()
        return round(end - self.started_at, 4)

    @property
    def match_rate(self) -> float:
        """Matched / staged, in [0, 1]. 0 when nothing was staged."""
        return round(self.rows_matched / self.rows_staged, 4) if self.rows_staged else 0.0

    def record_timing(self, stage: Stage, seconds: float, rows_in: int, rows_out: int) -> None:
        self.stage_timings.append(StageTiming(stage.value, round(seconds, 4), rows_in, rows_out))

    @contextmanager
    def time_stage(self, stage: Stage, rows_in: int) -> Iterator[list[int]]:
        """Time a stage; caller sets ``box[0]`` to the output row count.

        Usage::

            with metrics.time_stage(Stage.PARSE, rows_in) as box:
                ... ; box[0] = len(out)
        """
        box = [rows_in]
        start = time.perf_counter()
        try:
            yield box
        finally:
            self.record_timing(stage, time.perf_counter() - start, rows_in, box[0])

    def finalize(self, status: RunStatus = RunStatus.SUCCESS) -> None:
        self.finished_at = time.time()
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        """Flat, serializable view — used for the audit row and the log line."""
        return {
            "run_id": self.run_id,
            "provider_id": self.provider_id,
            "runtime": self.runtime,
            "source_uri": self.source_uri,
            "status": self.status.value,
            "duration_seconds": self.duration_seconds,
            "match_rate": self.match_rate,
            "rows_received": self.rows_received,
            "rows_rejected": self.rows_rejected,
            "rows_cleansed": self.rows_cleansed,
            "rows_landed": self.rows_landed,
            "rows_staged": self.rows_staged,
            "rows_matched": self.rows_matched,
            "rows_unmatched": self.rows_unmatched,
            "rows_skipped": self.rows_skipped,
            "stage_timings": [
                {
                    "stage": t.stage,
                    "seconds": t.seconds,
                    "rows_in": t.rows_in,
                    "rows_out": t.rows_out,
                }
                for t in self.stage_timings
            ],
            "dq_metrics": self.dq_metrics,
            "error": self.error,
        }
