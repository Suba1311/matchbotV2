"""Runtime selection by name."""

from __future__ import annotations

from matchbot.runtime.base import Runtime


def get_runtime(name: str) -> Runtime:
    """Return the runtime adapter for ``name`` (local | fargate | glue | snowflake)."""
    key = name.lower().strip()
    if key == "local":
        from matchbot.runtime.local import LocalRuntime

        return LocalRuntime()
    if key == "fargate":
        from matchbot.runtime.fargate import FargateRuntime

        return FargateRuntime()
    if key == "glue":
        from matchbot.runtime.glue import GlueRuntime

        return GlueRuntime()
    if key == "snowflake":
        from matchbot.runtime.snowflake import SnowflakeRuntime

        return SnowflakeRuntime()
    raise ValueError(f"Unknown runtime {name!r}. Choose one of: local, fargate, glue, snowflake.")
