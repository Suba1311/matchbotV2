"""The ETL pipeline: ordered, loosely-coupled stages + an orchestrator.

Each stage implements :class:`~matchbot.pipeline.base.PipelineStage` and
transforms a Polars frame, recording row counts and timings into the run's
:class:`~matchbot.audit.metrics.RunMetrics`. The orchestrator chains them and
owns the audit log; no stage knows about any other stage.
"""

from matchbot.pipeline.base import PipelineContext, PipelineStage, StageResult

__all__ = ["PipelineContext", "PipelineStage", "StageResult"]
