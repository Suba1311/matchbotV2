"""Storage layer: a backend-agnostic repository interface + a Postgres impl.

All database access in the pipeline goes through :class:`~matchbot.storage.base.Repository`.
The pipeline core never imports a concrete driver, so a Snowflake (or any other)
backend can be added later by implementing the same interface — no changes to
stages, matching, or the orchestrator.
"""

from matchbot.storage.base import Repository
from matchbot.storage.postgres import PostgresRepository

__all__ = ["PostgresRepository", "Repository"]
