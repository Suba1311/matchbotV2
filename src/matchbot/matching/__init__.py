"""Matching engine: standardizers, blocking, and the matcher chain.

* :mod:`standardize` — pure value normalizers (name/ssn/phonetic), ported from
  the legacy ``derived_identifiers`` but driven by config (gender map, suffixes).
* :mod:`blocking` — builds blocking keys to narrow candidate members.
* :mod:`base` / :mod:`deterministic` / :mod:`fuzzy` — the matcher protocol,
  a registry, and the two built-in matcher types.
"""

from matchbot.matching.base import Matcher, MatchOutcome, build_matchers, register_matcher

__all__ = ["MatchOutcome", "Matcher", "build_matchers", "register_matcher"]
