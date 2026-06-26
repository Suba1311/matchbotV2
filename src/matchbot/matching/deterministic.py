"""Deterministic matcher: all configured keys must agree exactly.

Config-driven (the keys come from :class:`MatcherSpec.keys`), so SSN+DOB,
name+DOB, or any other exact combination is declared in YAML, not code. A
candidate matches only if every key attribute is present and equal on both
sides; the first candidate to satisfy that wins.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from matchbot.config.models import MatcherSpec, StandardizationConfig
from matchbot.domain.enums import MatchDecision, MatchMethod
from matchbot.matching.base import NO_MATCH, MatchOutcome, register_matcher
from matchbot.matching.base import member_key as _member_key

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def _norm(value: Any) -> Any:
    """Normalize a value for exact comparison (uppercase/trim strings)."""
    if isinstance(value, str):
        return value.strip().upper()
    return value


@register_matcher("deterministic")
class DeterministicMatcher:
    """Exact-equality matcher over a configured set of key attributes."""

    def __init__(self, spec: MatcherSpec, std_config: StandardizationConfig) -> None:
        self.name = spec.name
        self.keys = list(spec.keys)
        self._std = std_config

    def match(
        self,
        record: Mapping[str, Any],
        candidates: Sequence[Mapping[str, Any]],
    ) -> MatchOutcome:
        # A record with any missing key cannot match deterministically.
        rec_vals = {}
        for k in self.keys:
            v = record.get(k)
            if v is None or (isinstance(v, str) and not v.strip()):
                return NO_MATCH
            rec_vals[k] = _norm(v)

        for cand in candidates:
            if all(_norm(cand.get(k)) == rec_vals[k] for k in self.keys):
                return MatchOutcome(
                    decision=MatchDecision.MATCHED,
                    method=MatchMethod.DETERMINISTIC,
                    member_id=_member_key(cand),
                    score=1.0,
                    reason=f"{self.name}: exact match on {'+'.join(self.keys)}",
                )
        return NO_MATCH
