"""Configuration layer: env-driven settings + layered YAML config.

Two distinct kinds of configuration:

* :class:`~matchbot.config.settings.Settings` — *environment* concerns
  (database URL, schema, log level, runtime). Comes from env vars / ``.env``.
* :class:`~matchbot.config.models.AppConfig` — *domain* concerns (canonical
  attributes, providers, matchers, DQ rules, thresholds). Comes from layered
  YAML and is validated on load.
"""

from matchbot.config.loader import load_config
from matchbot.config.models import (
    AppConfig,
    GlobalConfig,
    MatchConfig,
    ProviderConfig,
)
from matchbot.config.settings import Settings, get_settings

__all__ = [
    "AppConfig",
    "GlobalConfig",
    "MatchConfig",
    "ProviderConfig",
    "Settings",
    "get_settings",
    "load_config",
]
