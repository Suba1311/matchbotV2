"""Unit tests for blocking and the matcher chain."""

from __future__ import annotations

from typing import Any

from matchbot.config.models import AppConfig
from matchbot.domain.enums import MatchDecision
from matchbot.matching import blocking
from matchbot.matching.base import build_matchers


def _run_chain(config: AppConfig, record: dict[str, Any], members: list[dict[str, Any]]):
    g = config.global_config
    keys = g.matching.blocking_keys
    index = blocking.index_members(members, keys)
    matchers = build_matchers(g.matching.matchers, g.standardization)
    cands = [members[i] for i in blocking.candidate_indices(record, keys, index)]
    for m in matchers:
        out = m.match(record, cands)
        if out.decision in (MatchDecision.MATCHED, MatchDecision.AMBIGUOUS):
            return out
    return None


def test_deterministic_ssn_dob_match(
    app_config: AppConfig, members: list[dict[str, Any]]
) -> None:
    rec = {
        "first_name": "MARY",
        "last_name": "CONTRERAS",
        "birth_date": "1990-03-15",
        "ssn": "123456789",
    }
    out = _run_chain(app_config, rec, members)
    assert out is not None
    assert out.decision is MatchDecision.MATCHED
    assert out.member_id == "1"
    assert out.score == 1.0


def test_no_match_for_new_person(
    app_config: AppConfig, members: list[dict[str, Any]]
) -> None:
    rec = {
        "first_name": "ZELDA",
        "last_name": "NOBODY",
        "birth_date": "2001-01-01",
        "ssn": "555555555",
    }
    out = _run_chain(app_config, rec, members)
    assert out is None  # routed to UNMATCHED by the orchestrator


def test_blocking_narrows_candidates(
    app_config: AppConfig, members: list[dict[str, Any]]
) -> None:
    keys = app_config.global_config.matching.blocking_keys
    index = blocking.index_members(members, keys)
    rec = {"ssn": "123456789", "last_name": "CONTRERASRUIZ", "birth_date": "1990-03-15"}
    cand = blocking.candidate_indices(rec, keys, index)
    assert cand == [0]  # only M1 shares a blocking key


def test_block_value_incomplete_returns_none(app_config: AppConfig) -> None:
    key = next(
        k for k in app_config.global_config.matching.blocking_keys if len(k.attributes) > 1
    )
    # Missing one of the key's attributes -> no blocking value.
    assert blocking.block_value({}, key) is None
