"""Blocking-key generation.

Blocking keys cheaply narrow the Member Universe to plausible candidates before
the (more expensive) matcher chain runs. Keys are declared in config as tuples
of canonical attributes, optionally phonetically encoded. The same key function
is applied to both staged records and member rows; records and members sharing
any key value are candidates.

Ported in spirit from the legacy ``blocking_combinations`` but fully
config-driven.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from matchbot.matching import standardize as S

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from matchbot.config.models import BlockingKey


def _encode(attr: str, value: Any, phonetic_attrs: frozenset[str]) -> str | None:
    """Encode one attribute value for a blocking key."""
    if value is None or value == "":
        return None
    if attr in phonetic_attrs:
        return S.metaphone(str(value))
    if isinstance(value, str):
        return value.strip().upper()
    return str(value)


def block_value(record: Mapping[str, Any], key: BlockingKey) -> str | None:
    """Compute a single blocking-key value for a record, or None if incomplete.

    A key requiring an attribute the record lacks yields None (that record won't
    be blocked on that key) — never a partial/ambiguous key.
    """
    phonetic = frozenset(key.phonetic)
    parts: list[str] = []
    for attr in key.attributes:
        encoded = _encode(attr, record.get(attr), phonetic)
        if encoded is None:
            return None
        parts.append(encoded)
    return f"{key.name}:" + "|".join(parts)


def index_members(
    members: Sequence[Mapping[str, Any]],
    keys: Sequence[BlockingKey],
) -> dict[str, list[int]]:
    """Build an inverted index: blocking-key value -> member row indices."""
    index: dict[str, list[int]] = {}
    for i, member in enumerate(members):
        for key in keys:
            bv = block_value(member, key)
            if bv is not None:
                index.setdefault(bv, []).append(i)
    return index


def candidate_indices(
    record: Mapping[str, Any],
    keys: Sequence[BlockingKey],
    index: Mapping[str, list[int]],
) -> list[int]:
    """Return de-duplicated member indices that share any blocking key."""
    seen: dict[int, None] = {}
    for key in keys:
        bv = block_value(record, key)
        if bv is not None:
            for idx in index.get(bv, ()):
                seen.setdefault(idx, None)
    return list(seen)
