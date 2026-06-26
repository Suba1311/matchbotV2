"""Run metrics and audit-log persistence.

:class:`~matchbot.audit.metrics.RunMetrics` accumulates per-stage row counts,
timings, DQ metrics, and match rates during a run. At completion the
orchestrator persists one audit record (via the repository) and emits a single
structured JSON summary log line — the same surface on every runtime, which is
what makes Fargate / Glue / Snowflake benchmarking comparable.
"""

from matchbot.audit.metrics import RunMetrics, StageTiming

__all__ = ["RunMetrics", "StageTiming"]
