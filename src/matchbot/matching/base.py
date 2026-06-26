"""Matcher protocol, outcome type, and registry.

A matcher takes a single staged record (as a dict of canonical attributes) plus
the candidate Member Universe rows surfaced by blocking, and returns a
:class:`MatchOutcome`. Matchers are registered by ``type`` name so the chain is
assembled purely from config — adding a new matcher type is a new class +
``@register_matcher``, never an edit to the orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from matchbot.domain.enums import MatchDecision, MatchMethod

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from matchbot.config.models import MatcherSpec, StandardizationConfig


@dataclass(slots=True)
class MatchOutcome:
    """Result of running one matcher against a record's candidate set."""

    decision: MatchDecision
    method: MatchMethod
    member_id: str | None = None
    score: float = 0.0
    reason: str = ""
    matcher_name: str = ""


# Sentinel "no match" outcome reused to avoid allocations.
NO_MATCH = MatchOutcome(MatchDecision.UNMATCHED, MatchMethod.NONE, None, 0.0, "")


def member_key(candidate: Mapping[str, Any]) -> str | None:
    """Return a candidate member's primary key as a string.

    Reads ``id`` (the member_universe PK) first, falling back to ``member_id``
    so in-memory/test repositories using string member ids still work.
    """
    pk = candidate.get("id")
    if pk is None:
        pk = candidate.get("member_id")
    return None if pk is None else str(pk)


@runtime_checkable
class Matcher(Protocol):
    """A single matching strategy in the chain."""

    name: str

    def match(
        self,
        record: Mapping[str, Any],
        candidates: Sequence[Mapping[str, Any]],
    ) -> MatchOutcome:
        """Return the outcome of matching ``record`` against ``candidates``."""
        ...


# --- registry ---------------------------------------------------------------
_REGISTRY: dict[str, type] = {}


def register_matcher(type_name: str) -> Callable[[type], type]:
    """Register a matcher class under a ``type`` name used in config."""

    def _wrap(cls: type) -> type:
        if type_name in _REGISTRY:
            raise ValueError(f"matcher type {type_name!r} already registered")
        _REGISTRY[type_name] = cls
        return cls

    return _wrap


def build_matchers(
    specs: Sequence[MatcherSpec],
    std_config: StandardizationConfig,
) -> list[Matcher]:
    """Instantiate the enabled matchers, in order, from their specs."""
    # Import for side-effect registration of built-in matchers.
    from matchbot.matching import deterministic, fuzzy  # noqa: F401

    matchers: list[Matcher] = []
    for spec in specs:
        if not spec.enabled:
            continue
        try:
            cls = _REGISTRY[spec.type]
        except KeyError:
            known = ", ".join(sorted(_REGISTRY)) or "<none>"
            raise KeyError(
                f"Unknown matcher type {spec.type!r} for {spec.name!r}. Registered types: {known}"
            ) from None
        matchers.append(cls(spec, std_config))
    return matchers
