"""Unit tests for the fuzzy matcher's scoring and accept/review routing."""

from __future__ import annotations

from matchbot.config.models import FieldComparison, MatcherSpec, StandardizationConfig
from matchbot.domain.enums import MatchDecision
from matchbot.matching.fuzzy import FuzzyMatcher, _similarity

STD = StandardizationConfig()

SPEC = MatcherSpec(
    name="fuzzy_test",
    type="fuzzy",
    accept_threshold=0.80,
    review_threshold=0.50,
    comparisons=[
        FieldComparison(attribute="first_name", method="jaro_winkler", weight=1.0, threshold=0.90),
        FieldComparison(attribute="last_name", method="jaro_winkler", weight=1.0, threshold=0.90),
        FieldComparison(attribute="birth_date", method="exact", weight=2.0, threshold=1.0),
    ],
)


def test_similarity_methods() -> None:
    assert _similarity("exact", "ABC", "abc") == 1.0
    assert _similarity("exact", "ABC", "XYZ") == 0.0
    assert _similarity("jaro_winkler", "MARY", "MARI") > 0.8
    assert _similarity("levenshtein", "MARY", "MARY") == 1.0
    assert _similarity("metaphone", "Smith", "Smyth") == 1.0
    assert _similarity("exact", None, "x") == 0.0


def test_accept_when_strong() -> None:
    m = FuzzyMatcher(SPEC, STD)
    rec = {"first_name": "MARY", "last_name": "JONES", "birth_date": "1990-01-01"}
    cands = [
        {
            "member_id": "M9",
            "first_name": "MARY",
            "last_name": "JONES",
            "birth_date": "1990-01-01",
        }
    ]
    out = m.match(rec, cands)
    assert out.decision is MatchDecision.MATCHED
    assert out.idcol_id == "M9"
    assert out.score == 1.0


def test_review_when_borderline() -> None:
    m = FuzzyMatcher(SPEC, STD)
    # Only birth_date agrees (weight 2 of 4 = 0.5) -> >= review (0.5), < accept (0.8).
    rec = {"first_name": "AAA", "last_name": "BBB", "birth_date": "1990-01-01"}
    cands = [
        {
            "member_id": "M9",
            "first_name": "ZZZ",
            "last_name": "QQQ",
            "birth_date": "1990-01-01",
        }
    ]
    out = m.match(rec, cands)
    assert out.decision is MatchDecision.AMBIGUOUS
    assert out.score == 0.5


def test_unmatched_when_weak() -> None:
    m = FuzzyMatcher(SPEC, STD)
    rec = {"first_name": "AAA", "last_name": "BBB", "birth_date": "1990-01-01"}
    cands = [
        {
            "member_id": "M9",
            "first_name": "ZZZ",
            "last_name": "QQQ",
            "birth_date": "2000-12-31",
        }
    ]
    out = m.match(rec, cands)
    assert out.decision is MatchDecision.UNMATCHED


def test_no_candidates() -> None:
    m = FuzzyMatcher(SPEC, STD)
    out = m.match({"first_name": "X"}, [])
    assert out.decision is MatchDecision.UNMATCHED
