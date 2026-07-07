"""End-to-end pipeline test using the in-memory repository (no Postgres needed).

Exercises every stage and asserts the routing + audit metrics are correct, so CI
catches regressions without external infrastructure. Uses ride_enrollment (the
only provider configured today) — SASID-only exact matching, no SSN/DOB/address.
"""

from __future__ import annotations

from matchbot.config.models import AppConfig
from matchbot.config.settings import Settings
from matchbot.notify.base import Notifier
from matchbot.pipeline.orchestrator import Orchestrator
from matchbot.runtime.base import FileSystem
from tests.conftest import InMemoryRepository

# Minimal columns RIDE's column_mappings actually reads (FIRSTNAME, MIDDLENAME,
# LASTNAME, SASID, SEX) — every row has the same field count as the header,
# consistent with ParseStage's ragged-row validation.
CSV = (
    "FIRSTNAME,MIDDLENAME,LASTNAME,SASID,SEX\n"
    # exact match to reference row 1 (SASID)
    "MARY,,CONTRERAS,1000049302,F\n"
    # exact match to reference row 2 (SASID)
    "JOHN,,JONES,1000160573,M\n"
    # new person -> unmatched (SASID not in reference)
    "ZELDA,,NOBODY,9999999999,F\n"
)


class _DictFS(FileSystem):
    """A filesystem serving one in-memory CSV file."""

    def __init__(self, content: str) -> None:
        self._content = content.encode("utf-8")

    def list(self, uri: str, glob: str) -> list[str]:
        return ["mem://ride_enrollment_test.csv"]

    def read_bytes(self, uri: str) -> bytes:
        return self._content

    def write_bytes(self, uri: str, data: bytes) -> None:  # pragma: no cover
        pass


class _CollectNotifier(Notifier):
    def __init__(self) -> None:
        self.calls: list = []

    def notify(self, metrics) -> None:
        self.calls.append(metrics)


def test_full_pipeline_routes_and_audits(
    app_config: AppConfig, ride_repo: InMemoryRepository
) -> None:
    settings = Settings(_env_file=None)
    notifier = _CollectNotifier()
    orch = Orchestrator(app_config, settings, ride_repo, _DictFS(CSV), notifier)

    results = orch.run_provider("ride_enrollment", "mem://")
    assert len(results) == 1
    m = results[0].metrics

    # 3 rows in: 2 matched (SASID), 1 unmatched.
    assert m.rows_received == 3
    assert m.rows_staged == 3
    assert m.rows_matched == 2
    assert m.rows_unmatched == 1
    assert m.match_rate == round(2 / 3, 4)
    assert m.status.value == "success"

    # Full lifecycle persisted: land + stage + target + error.
    assert len(ride_repo.land) == 3
    assert len(ride_repo.stage) == 3
    assert len(ride_repo.target) == 2
    assert len(ride_repo.error) == 1
    assert {r["idcol_id"] for r in ride_repo.target} == {1, 2}
    assert all(
        r["match_method"] == "EXACT_SASID" for r in ride_repo.target
    )
    assert ride_repo.error[0]["decision"] == "NO_MATCH"

    # Stage rows updated in place + run finalized + notification fired.
    assert len(ride_repo.stage_updates) == 3
    assert len(ride_repo.finalized) == 1
    assert len(notifier.calls) == 1

    # Per-stage timings recorded for all four stages.
    stages = {t.stage for t in m.stage_timings}
    assert stages == {"parse", "canonical", "cleanse", "match"}

    # Only the SASID rule applies to RIDE's data (no SSN/DOB/address mapped).
    assert m.matched_on == ["SASID"]


def test_skip_if_null_drops_rows(
    app_config: AppConfig, ride_repo: InMemoryRepository
) -> None:
    csv = (
        "FIRSTNAME,MIDDLENAME,LASTNAME,SASID,SEX\n"
        "NOSASID,,PERSON,,F\n"  # missing SASID -> skipped
    )
    settings = Settings(_env_file=None)
    orch = Orchestrator(app_config, settings, ride_repo, _DictFS(csv), _CollectNotifier())
    results = orch.run_provider("ride_enrollment", "mem://")
    m = results[0].metrics
    assert m.rows_received == 1
    assert m.rows_skipped == 1
    assert m.rows_staged == 0
