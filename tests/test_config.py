"""Tests for config loading and fail-fast cross-validation."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from matchbot.config.loader import ConfigError, load_config
from matchbot.config.models import AppConfig
from matchbot.config.settings import Settings
from matchbot.domain.canonical import CANONICAL_ATTRIBUTES

# Derive the canonical list from the domain module so tests never drift when a
# new attribute is added.
_CANON = [{"name": a.name, "dtype": a.dtype} for a in CANONICAL_ATTRIBUTES]


def test_real_config_loads(app_config: AppConfig) -> None:
    assert {"ride_enrollment"} <= set(app_config.providers)
    assert len(app_config.global_config.matching.matchers) >= 3


def test_unknown_attribute_in_mapping_fails(tmp_path: Path) -> None:
    # Build a minimal valid config, then break one provider mapping.
    cfg = tmp_path / "config"
    (cfg / "providers").mkdir(parents=True)
    global_yaml = {
        "canonical_attributes": _CANON,
        "matching": {
            "matchers": [{"name": "d", "type": "deterministic", "keys": ["ssn"]}]
        },
    }
    (cfg / "global.yaml").write_text(yaml.safe_dump(global_yaml))
    bad_provider = {
        "provider_id": "p",
        "display_name": "P",
        "format": "csv",
        "file_glob": "p_*.csv",
        "column_mappings": {"COL": "not_a_real_attribute"},
    }
    (cfg / "providers" / "p.yaml").write_text(yaml.safe_dump(bad_provider))

    with pytest.raises(ConfigError, match="unknown attribute"):
        load_config(cfg)


def test_fixed_width_requires_columns(tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    (cfg / "providers").mkdir(parents=True)
    global_yaml = {
        "canonical_attributes": _CANON,
        "matching": {"matchers": [{"name": "d", "type": "deterministic", "keys": ["ssn"]}]},
    }
    (cfg / "global.yaml").write_text(yaml.safe_dump(global_yaml))
    prov = {
        "provider_id": "p",
        "display_name": "P",
        "format": "fixed_width",
        "file_glob": "p_*.txt",
        "column_mappings": {"X": "ssn"},
        # no fixed_width_columns -> should fail
    }
    (cfg / "providers" / "p.yaml").write_text(yaml.safe_dump(prov))
    with pytest.raises(ConfigError):
        load_config(cfg)


def test_schema_from_env_only() -> None:
    s = Settings(_env_file=None, DB_SCHEMA="tenant_a")
    assert s.db_schema == "tenant_a"


def test_invalid_schema_rejected() -> None:
    with pytest.raises(ValueError, match="alphanumeric"):
        Settings(_env_file=None, DB_SCHEMA="bad-schema!")
