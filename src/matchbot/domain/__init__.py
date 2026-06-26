"""Pure domain layer: canonical schema, enums, value objects.

This package has NO dependencies on Polars, cloud SDKs, or the database. It
defines the vocabulary the rest of the system speaks.
"""

from matchbot.domain.canonical import CANONICAL_ATTRIBUTES, CanonicalAttribute
from matchbot.domain.enums import MatchDecision, MatchMethod, Stage

__all__ = [
    "CANONICAL_ATTRIBUTES",
    "CanonicalAttribute",
    "MatchDecision",
    "MatchMethod",
    "Stage",
]
