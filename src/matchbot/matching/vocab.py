"""Mapping between internal match outcomes and the DB vocabulary.

The pipeline reasons in terms of :class:`MatchDecision` / :class:`MatchMethod`;
the stage/target/error tables use the agreed string vocabulary
(EXACT_SASID / LEVENSHTEIN / ... and MATCHED / LOW_CONFIDENCE / NO_MATCH).
Centralized here so the two never drift and the mapping is easy to extend.
"""

from __future__ import annotations

from matchbot.domain.enums import MatchDecision, MatchMethod

# stage.match_status / error.decision vocabulary
STATUS_MATCHED = "MATCHED"
STATUS_LOW_CONFIDENCE = "LOW_CONFIDENCE"
STATUS_NO_MATCH = "NO_MATCH"
STATUS_PENDING = "PENDING"


def decision_to_status(decision: MatchDecision) -> str:
    return {
        MatchDecision.MATCHED: STATUS_MATCHED,
        MatchDecision.AMBIGUOUS: STATUS_LOW_CONFIDENCE,
        MatchDecision.UNMATCHED: STATUS_NO_MATCH,
    }[decision]


def method_to_db(method: MatchMethod, matcher_name: str) -> str:
    """Map an internal method + matcher name to the target.match_method vocab."""
    if method is MatchMethod.DETERMINISTIC:
        # Distinguish the SASID/external-id matcher from other deterministic keys.
        if "external_id" in matcher_name or "sasid" in matcher_name:
            return "EXACT_SASID"
        return "EXACT"
    if method is MatchMethod.FUZZY:
        return "LEVENSHTEIN"
    if method is MatchMethod.SEMANTIC:
        return "SEMANTIC"
    return "NONE"
