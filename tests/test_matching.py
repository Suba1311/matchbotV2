"""Unit tests for blocking and the matcher chain (RIDE: SASID-only matching)."""

from __future__ import annotations

from typing import Any

from matchbot.config.models import AppConfig
from matchbot.domain.enums import MatchDecision
from matchbot.matching import blocking
from matchbot.matching.base import build_matchers
from matchbot.pipeline.match import filter_chain_by_provider_attributes


def _with_member_external_id(reference_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Mirror PostgresRepository/InMemoryRepository.load_reference()'s
    sasid -> member_external_id aliasing. rilds_reference rows only ever have
    ``sasid`` natively; blocking/matching key on the canonical
    ``member_external_id`` name, so tests must apply the same aliasing the
    real repositories do rather than pass raw fixture rows straight through."""
    out = []
    for r in reference_rows:
        d = dict(r)
        d.setdefault("member_external_id", d.get("sasid"))
        out.append(d)
    return out


def _run_chain(config: AppConfig, record: dict[str, Any], reference_rows: list[dict[str, Any]]):
    g = config.global_config
    provider = config.provider("ride_enrollment")
    keys = g.matching.blocking_keys
    candidates = _with_member_external_id(reference_rows)
    index = blocking.index_members(candidates, keys)
    mapped_attributes = set(provider.column_mappings.values())
    chain = filter_chain_by_provider_attributes(g.matching.matchers, mapped_attributes)
    matchers = build_matchers(chain, g.standardization)
    cands = [candidates[i] for i in blocking.candidate_indices(record, keys, index)]
    for m in matchers:
        out = m.match(record, cands)
        if out.decision in (MatchDecision.MATCHED, MatchDecision.AMBIGUOUS):
            return out
    return None


def test_deterministic_sasid_match(
    app_config: AppConfig, ride_reference_rows: list[dict[str, Any]]
) -> None:
    rec = {"first_name": "MARY", "last_name": "CONTRERAS", "member_external_id": "1000049302"}
    out = _run_chain(app_config, rec, ride_reference_rows)
    assert out is not None
    assert out.decision is MatchDecision.MATCHED
    assert out.idcol_id == "1"
    assert out.score == 1.0


def test_no_match_for_new_person(
    app_config: AppConfig, ride_reference_rows: list[dict[str, Any]]
) -> None:
    rec = {"first_name": "ZELDA", "last_name": "NOBODY", "member_external_id": "9999999999"}
    out = _run_chain(app_config, rec, ride_reference_rows)
    assert out is None  # routed to UNMATCHED by the orchestrator


def test_blocking_narrows_candidates(
    app_config: AppConfig, ride_reference_rows: list[dict[str, Any]]
) -> None:
    keys = app_config.global_config.matching.blocking_keys
    candidates = _with_member_external_id(ride_reference_rows)
    index = blocking.index_members(candidates, keys)
    rec = {"member_external_id": "1000049302", "last_name": "CONTRERAS"}
    cand = blocking.candidate_indices(rec, keys, index)
    assert cand == [0]  # only reference row 1 shares a blocking key


def test_block_value_incomplete_returns_none(app_config: AppConfig) -> None:
    key = next(
        k for k in app_config.global_config.matching.blocking_keys if len(k.attributes) > 1
    )
    # Missing one of the key's attributes -> no blocking value.
    assert blocking.block_value({}, key) is None


def test_ride_chain_filters_to_sasid_only(app_config: AppConfig) -> None:
    """RIDE maps no ssn/birth_date/address1 — those rules must be dropped,
    leaving only deterministic_external_id in the resolved chain."""
    g = app_config.global_config
    provider = app_config.provider("ride_enrollment")
    mapped_attributes = set(provider.column_mappings.values())
    chain = filter_chain_by_provider_attributes(g.matching.matchers, mapped_attributes)
    assert [m.name for m in chain] == ["deterministic_external_id"]
