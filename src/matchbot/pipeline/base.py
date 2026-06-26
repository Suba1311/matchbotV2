"""Stage protocol, shared context, and result type.

The contract is deliberately tiny so stages stay loosely coupled and easy to
add/reorder/replace:

* A stage receives a :class:`PipelineContext` (config, settings, run id, the
  provider being processed, the repository, and the live metrics) plus the
  incoming Polars frame, and returns a :class:`StageResult` (the outgoing frame
  plus optional side outputs like the matched/error splits).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import polars as pl

if TYPE_CHECKING:
    from matchbot.audit.metrics import RunMetrics
    from matchbot.config.models import AppConfig, ProviderConfig
    from matchbot.config.settings import Settings
    from matchbot.domain.enums import Stage
    from matchbot.storage.base import Repository


@dataclass(slots=True)
class PipelineContext:
    """Everything a stage needs, assembled once per run by the orchestrator."""

    run_id: str
    provider: ProviderConfig
    config: AppConfig
    settings: Settings
    repository: Repository
    metrics: RunMetrics
    # arbitrary cross-stage scratch space (kept small, typed where it matters)
    state: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class StageResult:
    """A stage's output: the primary frame plus optional named side frames.

    The match stage uses ``side_outputs`` to return the TARGET and ERROR splits
    without forcing every other stage to know those concepts exist.
    """

    frame: pl.DataFrame
    side_outputs: dict[str, pl.DataFrame] = field(default_factory=dict)


@runtime_checkable
class PipelineStage(Protocol):
    """A single transformation step in the pipeline."""

    #: Which :class:`~matchbot.domain.enums.Stage` this implements (for audit).
    stage: Stage

    def run(self, ctx: PipelineContext, frame: pl.DataFrame) -> StageResult:
        """Transform ``frame`` and return the result."""
        ...
