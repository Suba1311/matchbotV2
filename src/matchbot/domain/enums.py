"""Enumerations shared across the pipeline."""

from __future__ import annotations

from enum import StrEnum


class Stage(StrEnum):
    """The pipeline stages, in execution order.

    Mirrors the reference architecture: Parse -> Cleanse -> Map to Canonical
    -> Match. RECEIVED is the implicit pre-parse hop used for audit row counts.
    """

    RECEIVED = "received"
    PARSE = "parse"
    CLEANSE = "cleanse"
    CANONICAL = "canonical"
    MATCH = "match"


class MatchMethod(StrEnum):
    """How a candidate match was produced."""

    DETERMINISTIC = "deterministic"
    FUZZY = "fuzzy"
    SEMANTIC = "semantic"
    NONE = "none"


class MatchDecision(StrEnum):
    """Where a record is routed after matching.

    There is no human gate in the pipeline. MATCHED records go to TARGET;
    everything else goes to ERROR for optional async review and never blocks
    the run.
    """

    MATCHED = "matched"
    UNMATCHED = "unmatched"
    AMBIGUOUS = "ambiguous"


class FileFormat(StrEnum):
    """Supported provider file formats."""

    CSV = "csv"
    XLSX = "xlsx"
    FIXED_WIDTH = "fixed_width"


class RunStatus(StrEnum):
    """Terminal status of a pipeline run, recorded in the audit log."""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
