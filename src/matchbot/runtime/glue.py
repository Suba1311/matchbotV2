"""AWS Glue runtime.

Glue here is "managed Python on a schedule": the MatchBot core is pure Polars
(no Spark dependency), so I/O is identical to Fargate's — S3 + RDS Postgres.
Reuses S3FileSystem and the Postgres repository as-is; the only Glue-specific
piece is the job entrypoint (see scripts/glue_job.py), which parses its own
``--key value`` job arguments from ``sys.argv`` — no Spark/GlueContext APIs
are involved, so this runs the same under a Spark job type or Python shell.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from matchbot.runtime.base import FileSystem, Runtime
from matchbot.runtime.fargate import S3FileSystem
from matchbot.storage.postgres import make_repository

if TYPE_CHECKING:
    from matchbot.config.settings import Settings
    from matchbot.storage.base import Repository


class GlueRuntime(Runtime):
    name = "glue"

    def filesystem(self) -> FileSystem:
        return S3FileSystem()

    def repository(self, settings: Settings) -> Repository:
        return make_repository(settings)
