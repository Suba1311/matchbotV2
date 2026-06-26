"""Snowflake runtime — stub.

Two viable shapes, both behind the same interfaces:

* **Pushdown**: implement a SnowflakeRepository (Repository subclass) whose
  ``load_member_universe`` / ``write_target`` / ``write_error`` / ``write_audit``
  run against Snowflake via the connector, and a StageFileSystem that reads from
  a Snowflake stage. The Polars matching core stays unchanged.
* **Snowpark**: run the same pure-Python code inside a Snowpark Python procedure;
  I/O becomes Snowpark DataFrames at the edges while the matcher chain stays as
  is.

Install with the ``[snowflake]`` extra. Left as a stub so selection works and
the implementation slot is explicit.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from matchbot.runtime.base import FileSystem, Runtime

if TYPE_CHECKING:
    from matchbot.config.settings import Settings
    from matchbot.storage.base import Repository

_MSG = (
    "SnowflakeRuntime is not yet implemented. Implement a SnowflakeRepository "
    "(storage.base.Repository) using snowflake-connector-python and a stage-backed "
    "FileSystem, or run the core inside a Snowpark procedure. Install with: "
    "pip install 'matchbot[snowflake]'."
)


class SnowflakeRuntime(Runtime):
    name = "snowflake"

    def filesystem(self) -> FileSystem:
        raise NotImplementedError(_MSG)

    def repository(self, settings: Settings) -> Repository:
        raise NotImplementedError(_MSG)
