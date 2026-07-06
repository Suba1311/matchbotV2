"""End-to-end pipeline test using the in-memory repository (no Postgres needed).

Exercises every stage and asserts the routing + audit metrics are correct, so CI
catches regressions without external infrastructure.
"""

from __future__ import annotations

from matchbot.config.models import AppConfig
from matchbot.config.settings import Settings
from matchbot.notify.base import Notifier
from matchbot.pipeline.orchestrator import Orchestrator
from matchbot.runtime.base import FileSystem
from tests.conftest import InMemoryRepository

CSV = (
    "FIRST_NAME,LAST_NAME,DOB,SSN,GENDER,ADDR1,CITY,STATE,ZIP\n"
    # exact match to M1 (ssn+dob)
    "MARY,CONTRERAS,3/15/1990,123-45-6789,F,1 ST,PROV,RI,02903\n"
    # exact match to M2
    "JOHN,JONES,7/22/1985,987-65-4321,M,2 AVE,PROV,RI,02903\n"
    # new person -> unmatched
    "ZELDA,NOBODY,1/1/2001,555-55-5555,F,3 RD,PROV,RI,02903\n"
)


class _DictFS(FileSystem):
    """A filesystem serving one in-memory CSV file."""

    def __init__(self, content: str) -> None:
        self._content = content.encode("utf-8")

    def list(self, uri: str, glob: str) -> list[str]:
        return ["mem://provider2_test.csv"]

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
    app_config: AppConfig, repo: InMemoryRepository
) -> None:
    settings = Settings(_env_file=None)
    notifier = _CollectNotifier()
    orch = Orchestrator(app_config, settings, repo, _DictFS(CSV), notifier)

    results = orch.run_provider("provider2_unemployment", "mem://")
    assert len(results) == 1
    m = results[0].metrics

    # 3 rows in: 2 matched (M1, M2), 1 unmatched.
    assert m.rows_received == 3
    assert m.rows_staged == 3
    assert m.rows_matched == 2
    assert m.rows_unmatched == 1
    assert m.match_rate == round(2 / 3, 4)
    assert m.status.value == "success"

    # Full lifecycle persisted: land + stage + target + error.
    assert len(repo.land) == 3
    assert len(repo.stage) == 3
    assert len(repo.target) == 2
    assert len(repo.error) == 1
    assert {r["idcol_id"] for r in repo.target} == {1, 2}
    assert all(r["match_method"] == "EXACT" or "EXACT" in r["match_method"] for r in repo.target)
    assert repo.error[0]["decision"] == "NO_MATCH"

    # Stage rows updated in place + run finalized + notification fired.
    assert len(repo.stage_updates) == 3
    assert len(repo.finalized) == 1
    assert len(notifier.calls) == 1

    # Per-stage timings recorded for all four stages.
    stages = {t.stage for t in m.stage_timings}
    assert stages == {"parse", "canonical", "cleanse", "match"}


def test_skip_if_null_drops_rows(
    app_config: AppConfig, repo: InMemoryRepository
) -> None:
    csv = (
        "FIRST_NAME,LAST_NAME,DOB,SSN,GENDER,ADDR1,CITY,STATE,ZIP\n"
        "NOSSN,PERSON,1/1/1990,,F,1 ST,PROV,RI,02903\n"  # missing ssn -> skipped
    )
    settings = Settings(_env_file=None)
    orch = Orchestrator(app_config, settings, repo, _DictFS(csv), _CollectNotifier())
    results = orch.run_provider("provider2_unemployment", "mem://")
    m = results[0].metrics
    assert m.rows_received == 1
    assert m.rows_skipped == 1
    assert m.rows_staged == 0
