"""Notifier interface + a log-only default."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from matchbot.logging_setup import get_logger

if TYPE_CHECKING:
    from matchbot.audit.metrics import RunMetrics

log = get_logger(__name__)


class Notifier(ABC):
    """Notify an operator that a run finished."""

    @abstractmethod
    def notify(self, metrics: RunMetrics) -> None: ...


class LogNotifier(Notifier):
    """Default notifier: emit the run summary as one structured log line."""

    def notify(self, metrics: RunMetrics) -> None:
        log.info("run.completed", **metrics.to_dict())
