"""AWS Glue runtime — stub.

Glue 5.0 runs Python 3.11 and provides Spark. The MatchBot core is pure Polars,
so the Glue adapter's job is the SAME as Fargate's for I/O (S3 + RDS); the only
real differences are (a) reading job arguments via ``getResolvedOptions`` and
(b) packaging the pure-Python wheel as an ``--additional-python-modules`` dep
rather than baking a container. Because the core doesn't use Spark, Glue here is
"managed Python on a schedule" — implement by reusing S3FileSystem and the
Postgres repository, plus a thin entry that parses Glue job args.

Left as a stub so the interface exists and selection works; fill in when the
Glue path is exercised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from matchbot.runtime.base import FileSystem, Runtime

if TYPE_CHECKING:
    from matchbot.config.settings import Settings
    from matchbot.storage.base import Repository

_MSG = (
    "GlueRuntime is not yet implemented. The core pipeline is Polars-based and "
    "platform-agnostic; to enable Glue, reuse S3FileSystem (runtime.fargate) and "
    "the Postgres repository, and add a Glue entrypoint that parses job args via "
    "awsglue.utils.getResolvedOptions. Package the wheel via "
    "--additional-python-modules."
)


class GlueRuntime(Runtime):
    name = "glue"

    def filesystem(self) -> FileSystem:
        raise NotImplementedError(_MSG)

    def repository(self, settings: Settings) -> Repository:
        raise NotImplementedError(_MSG)
