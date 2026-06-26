"""Shared fixtures: a real loaded config and an in-memory repository.

The in-memory repository lets the full pipeline run in tests without Postgres,
so CI needs no database. It implements the same interface as the Postgres repo.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from matchbot.audit.metrics import RunMetrics
from matchbot.config.loader import load_config
from matchbot.config.models import AppConfig
from matchbot.storage.base import Repository

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def app_config() -> AppConfig:
    return load_config(CONFIG_DIR)


class InMemoryRepository(Repository):
    """A Repository backed by Python lists — for tests and dry runs.

    Mirrors the Postgres repository's LAND -> STAGE -> TARGET/ERROR lifecycle,
    issuing integer ids for runs/stage rows so the orchestrator behaves exactly
    as it does against Postgres.
    """

    def __init__(self, members: list[dict[str, Any]] | None = None) -> None:
        self.members = members or []
        self.land: list[dict[str, Any]] = []
        self.stage: list[dict[str, Any]] = []
        self.stage_updates: list[dict[str, Any]] = []
        self.target: list[dict[str, Any]] = []
        self.error: list[dict[str, Any]] = []
        self.runs: list[dict[str, Any]] = []
        self.finalized: list[RunMetrics] = []
        self._run_seq = 0
        self._stage_seq = 0

    def init_schema(self) -> None:
        pass

    def begin_run(
        self, *, run_uid: str, provider_code: str, dataset_name: str, runtime: str,
        source_uri: str,
    ) -> int:
        self._run_seq += 1
        self.runs.append({"id": self._run_seq, "run_uid": run_uid, "status": "RUNNING"})
        return self._run_seq

    def write_land(
        self, *, pipeline_run_id: int, provider_code: str,
        source_columns: Sequence[str], rows: Sequence[Mapping[str, Any]],
    ) -> int:
        self.land.extend(dict(r) for r in rows)
        return len(rows)

    def write_stage(
        self, pipeline_run_id: int, rows: Sequence[Mapping[str, Any]]
    ) -> list[int]:
        ids = []
        for r in rows:
            self._stage_seq += 1
            self.stage.append({**dict(r), "id": self._stage_seq})
            ids.append(self._stage_seq)
        return ids

    def update_stage_matches(self, updates: Sequence[Mapping[str, Any]]) -> int:
        self.stage_updates.extend(dict(u) for u in updates)
        return len(updates)

    def load_member_universe(self) -> list[dict[str, Any]]:
        return list(self.members)

    def seed_member_universe(
        self, rows: Sequence[Mapping[str, Any]], *, replace: bool = True
    ) -> int:
        if replace:
            self.members = []
        self.members.extend(dict(r) for r in rows)
        return len(rows)

    def write_target(self, rows: Sequence[Mapping[str, Any]]) -> int:
        self.target.extend(dict(r) for r in rows)
        return len(rows)

    def write_error(self, rows: Sequence[Mapping[str, Any]]) -> int:
        self.error.extend(dict(r) for r in rows)
        return len(rows)

    def finalize_run(self, pipeline_run_id: int, metrics: RunMetrics) -> None:
        self.finalized.append(metrics)

    def close(self) -> None:
        pass


@pytest.fixture
def members() -> list[dict[str, Any]]:
    return [
        {
            "id": 1,
            "first_name": "MARYELLEN",
            "last_name": "CONTRERASRUIZ",
            "birth_date": "1990-03-15",
            "ssn": "123456789",
        },
        {
            "id": 2,
            "first_name": "JOHN",
            "last_name": "JONES",
            "birth_date": "1985-07-22",
            "ssn": "987654321",
        },
    ]


@pytest.fixture
def repo(members: list[dict[str, Any]]) -> InMemoryRepository:
    return InMemoryRepository(members=members)
