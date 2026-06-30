"""Load and cross-validate the layered YAML configuration.

Layout::

    config/
      global.yaml            # GlobalConfig
      providers/
        provider1.yaml       # ProviderConfig
        provider2.yaml
        ...

Beyond per-file Pydantic validation, this module performs *cross-file* checks
that catch the mistakes that actually bite during onboarding:

* a provider maps a column to a canonical attribute that doesn't exist;
* a transform / dq rule / matcher references an unknown attribute;
* the config's canonical list drifts from the domain module.

Any failure raises :class:`ConfigError` with a precise, actionable message
*before* the pipeline touches data.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from matchbot.config.models import AppConfig, GlobalConfig, MatcherSpec, ProviderConfig
from matchbot.domain.canonical import CANONICAL_NAMES
from matchbot.matching.derive import DERIVED_COLUMNS

# Attributes valid in matcher keys/comparisons: canonical + derived blocking columns.
_MATCHER_VALID_ATTRS = CANONICAL_NAMES | frozenset(DERIVED_COLUMNS)


class ConfigError(Exception):
    """Raised when configuration is missing, malformed, or inconsistent."""


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if data is None:
        raise ConfigError(f"Config file is empty: {path}")
    if not isinstance(data, dict):
        raise ConfigError(f"Config file must be a mapping at top level: {path}")
    return data


def _load_global(config_dir: Path) -> GlobalConfig:
    path = config_dir / "global.yaml"
    raw = _read_yaml(path)
    try:
        return GlobalConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid global config ({path}):\n{exc}") from exc


def _load_providers(config_dir: Path) -> dict[str, ProviderConfig]:
    providers_dir = config_dir / "providers"
    if not providers_dir.is_dir():
        raise ConfigError(f"Providers directory not found: {providers_dir}")

    providers: dict[str, ProviderConfig] = {}
    files = sorted(providers_dir.glob("*.yaml")) + sorted(providers_dir.glob("*.yml"))
    if not files:
        raise ConfigError(f"No provider YAML files found in {providers_dir}")

    for path in files:
        raw = _read_yaml(path)
        try:
            provider = ProviderConfig.model_validate(raw)
        except ValidationError as exc:
            raise ConfigError(f"Invalid provider config ({path}):\n{exc}") from exc
        if provider.provider_id in providers:
            raise ConfigError(
                f"Duplicate provider_id {provider.provider_id!r} (seen again in {path})"
            )
        providers[provider.provider_id] = provider
    return providers


def _validate_canonical_alignment(gconf: GlobalConfig) -> None:
    """Ensure config's canonical list matches the domain module exactly."""
    config_names = {a.name for a in gconf.canonical_attributes}
    missing = CANONICAL_NAMES - config_names
    extra = config_names - CANONICAL_NAMES
    problems = []
    if missing:
        problems.append(f"missing from config: {sorted(missing)}")
    if extra:
        problems.append(f"unknown to domain: {sorted(extra)}")
    if problems:
        raise ConfigError(
            "Canonical attributes in global.yaml disagree with the domain "
            "module (matchbot.domain.canonical): " + "; ".join(problems)
        )


def _validate_matcher_spec(spec: MatcherSpec, context: str, errors: list[str]) -> None:
    """Validate a single MatcherSpec's attribute references."""
    for k in spec.keys:
        if k not in _MATCHER_VALID_ATTRS:
            errors.append(f"{context} matcher {spec.name!r}: unknown key {k!r}")
    for c in spec.comparisons:
        if c.attribute not in _MATCHER_VALID_ATTRS:
            errors.append(
                f"{context} matcher {spec.name!r}: unknown comparison attribute {c.attribute!r}"
            )


def _validate_cross_references(app: AppConfig) -> None:
    """Check every attribute reference resolves to a canonical or derived attribute."""
    errors: list[str] = []

    g = app.global_config
    for bk in g.matching.blocking_keys:
        for attr in bk.attributes:
            if attr not in CANONICAL_NAMES:
                errors.append(f"blocking_key {bk.name!r}: unknown attribute {attr!r}")
    for m in g.matching.matchers:
        _validate_matcher_spec(m, "global", errors)
    for rule in g.dq_rules:
        if rule.attribute not in CANONICAL_NAMES:
            errors.append(f"dq_rule {rule.name!r}: unknown attribute {rule.attribute!r}")

    for pid, prov in app.providers.items():
        for col, attr in prov.column_mappings.items():
            if attr not in CANONICAL_NAMES:
                errors.append(
                    f"provider {pid!r}: column {col!r} maps to unknown attribute {attr!r}"
                )
        for attr in prov.transforms:
            if attr not in CANONICAL_NAMES:
                errors.append(f"provider {pid!r}: transform for unknown attribute {attr!r}")
        for attr in prov.skip_if_null:
            if attr not in CANONICAL_NAMES:
                errors.append(f"provider {pid!r}: skip_if_null unknown attribute {attr!r}")
        if prov.matchers:
            known_global = {m.name for m in g.matching.matchers}
            for entry in prov.matchers:
                if isinstance(entry, str):
                    # reference to a global matcher — must exist
                    if entry not in known_global:
                        errors.append(
                            f"provider {pid!r}: references unknown global matcher {entry!r}"
                        )
                else:
                    # inline MatcherSpec — validate its attribute references
                    _validate_matcher_spec(entry, f"provider {pid!r}", errors)

    if errors:
        raise ConfigError("Configuration cross-reference errors:\n  - " + "\n  - ".join(errors))


def load_config(config_dir: str | Path) -> AppConfig:
    """Load, validate, and cross-check the full application config.

    Parameters
    ----------
    config_dir:
        Directory containing ``global.yaml`` and ``providers/``.

    Raises
    ------
    ConfigError
        On any missing file, schema violation, or cross-reference mismatch.
    """
    config_dir = Path(config_dir)
    gconf = _load_global(config_dir)
    _validate_canonical_alignment(gconf)
    providers = _load_providers(config_dir)
    app = AppConfig(global_config=gconf, providers=providers)
    _validate_cross_references(app)
    return app
