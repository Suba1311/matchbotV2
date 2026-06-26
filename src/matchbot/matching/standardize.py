"""Value standardizers — pure functions, config-driven where the legacy system
hardcoded dictionaries (gender map, name suffixes/prefixes).

Ported from the legacy ``identifiers/models/derived_identifiers.py`` but with no
framework coupling: each function takes a string (and config where needed) and
returns a normalized string. These are used both by the cleanse stage (to
populate standardized columns) and by the matchers (to compare).
"""

from __future__ import annotations

import re

import jellyfish

from matchbot.config.models import StandardizationConfig

_WS_RE = re.compile(r"\s+")
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")
_DIGITS_RE = re.compile(r"\D")


def squash_ws(value: str) -> str:
    """Collapse internal whitespace and strip ends."""
    return _WS_RE.sub(" ", value).strip()


def std_name(value: str | None, std_config: StandardizationConfig) -> str | None:
    """Standardize a name: uppercase, strip prefixes/suffixes, remove non-alnum.

    Mirrors the legacy ``set_*_name_std``: all caps, whitespace stripped,
    suffixes (JR/SR/III...) and prefixes (MR/DR...) removed. Returns None for
    empty input.
    """
    if value is None:
        return None
    text = squash_ws(value).upper()
    if not text:
        return None

    tokens = text.split(" ")
    suffixes = {s.upper() for s in std_config.name_suffixes}
    prefixes = {p.upper() for p in std_config.name_prefixes}
    tokens = [t for t in tokens if t.strip(".") not in suffixes]
    if len(tokens) > 1 and tokens[0].strip(".") in prefixes:
        tokens = tokens[1:]

    joined = "".join(tokens)
    cleaned = _NON_ALNUM_RE.sub("", joined)
    return cleaned or None


def name_suffix(value: str | None, std_config: StandardizationConfig) -> str | None:
    """Return the suffix found in a name, if any (e.g. 'smith jr' -> 'JR')."""
    if value is None:
        return None
    suffixes = {s.upper() for s in std_config.name_suffixes}
    for token in squash_ws(value).upper().split(" "):
        if token.strip(".") in suffixes:
            return token.strip(".")
    return None


def std_ssn(value: str | None, *, width: int = 9) -> str | None:
    """Strip non-digits and left-pad to ``width``. None for empty input.

    Returns None (treated as unusable) if the result is not exactly ``width``
    digits, so malformed SSNs don't create false blocking collisions.
    """
    if value is None:
        return None
    digits = _DIGITS_RE.sub("", value)
    if not digits:
        return None
    digits = digits.zfill(width)
    return digits if len(digits) == width else None


def ssn4(value: str | None) -> str | None:
    """Last four of a standardized SSN."""
    std = std_ssn(value)
    return std[-4:] if std else None


def metaphone(value: str | None) -> str | None:
    """Primary double-metaphone code (phonetic), used for blocking/fuzzy."""
    if value is None:
        return None
    text = squash_ws(value).upper()
    if not text:
        return None
    primary, _secondary = jellyfish.metaphone(text), None
    return primary or None


def std_gender(value: str | None, std_config: StandardizationConfig) -> str | None:
    """Map a raw gender token via the configured gender map (case-insensitive)."""
    if value is None:
        return None
    text = squash_ws(value).upper()
    if not text:
        return None
    upper_map = {k.upper(): v for k, v in std_config.gender_map.items()}
    return upper_map.get(text, text)


def jaro_winkler(a: str | None, b: str | None) -> float:
    """Jaro-Winkler similarity in [0, 1]; 0 if either side is missing."""
    if not a or not b:
        return 0.0
    return float(jellyfish.jaro_winkler_similarity(a, b))
