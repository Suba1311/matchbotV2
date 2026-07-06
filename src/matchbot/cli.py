"""MatchBot command-line interface.

A single orchestrated entrypoint — no manual per-stage steps:

* ``matchbot run``            — run the full pipeline for a provider's files.
* ``matchbot init-db``        — create schema + tables (idempotent).
* ``matchbot validate-config`` — load and cross-validate all config, fail-fast.
* ``matchbot list-providers`` — show configured providers.
"""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from matchbot.config.loader import ConfigError, load_config
from matchbot.config.settings import Settings, get_settings
from matchbot.logging_setup import configure_logging, get_logger
from matchbot.notify.factory import get_notifier
from matchbot.runtime.factory import get_runtime

app = typer.Typer(
    name="matchbot",
    help="Multi-provider member-matching ETL pipeline.",
    no_args_is_help=True,
    add_completion=False,
)
log = get_logger("matchbot.cli")


def _bootstrap() -> Settings:
    """Configure logging from settings; return settings."""
    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    return settings


@app.command()
def run(
    provider: Annotated[str, typer.Option(help="Provider id to process.")],
    input: Annotated[str, typer.Option(help="Input directory or file URI (file://, s3://).")],
) -> None:
    """Run the full pipeline for PROVIDER over files under INPUT."""
    settings = _bootstrap()
    try:
        config = load_config(settings.config_dir)
    except ConfigError as exc:
        typer.secho(f"Config error:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc

    # Lazy import so the core stays free of orchestrator import cost at startup.
    from matchbot.pipeline.orchestrator import Orchestrator

    runtime = get_runtime(settings.runtime)
    fs = runtime.filesystem()
    notifier = get_notifier(settings)
    failures = 0
    with runtime.repository(settings) as repo:
        # Idempotent: create_all()'s checkfirst=True skips anything that
        # already exists (e.g. a pre-populated rilds_reference), so this is
        # cheap and safe to call on every run — no separate init-db step
        # required before the first run against a fresh database.
        repo.init_schema()
        orch = Orchestrator(config, settings, repo, fs, notifier)
        try:
            results = orch.run_provider(provider, input)
        except Exception as exc:
            typer.secho(f"Run failed: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc

    if not results:
        typer.secho("No files processed.", fg=typer.colors.YELLOW)
        return

    typer.secho(f"\nProcessed {len(results)} file(s):", fg=typer.colors.GREEN)
    for r in results:
        m = r.metrics
        if m.status.value != "success":
            failures += 1
        typer.echo(
            f"  {r.source_uri}: {m.rows_matched}/{m.rows_staged} matched "
            f"({m.match_rate:.1%}), {m.rows_unmatched} unmatched, "
            f"{m.duration_seconds}s [{m.status.value}]"
        )
    if failures:
        raise typer.Exit(1)


@app.command(name="init-db")
def init_db() -> None:
    """Create the schema and tables in the configured DB_SCHEMA (idempotent)."""
    settings = _bootstrap()
    runtime = get_runtime(settings.runtime)
    with runtime.repository(settings) as repo:
        repo.init_schema()
    typer.secho(f"Initialized schema '{settings.db_schema}'.", fg=typer.colors.GREEN)


@app.command(name="seed-members")
def seed_members(
    csv: Annotated[str, typer.Option(help="Path to a member-universe CSV.")],
    replace: Annotated[
        bool, typer.Option(help="Truncate existing members before loading.")
    ] = True,
) -> None:
    """Load the Member Universe from a CSV (dev/bootstrap helper)."""
    import io

    import polars as pl

    settings = _bootstrap()
    runtime = get_runtime(settings.runtime)
    fs = runtime.filesystem()
    csv_bytes = fs.read_bytes(csv)
    df = pl.read_csv(io.BytesIO(csv_bytes), infer_schema_length=0)
    with runtime.repository(settings) as repo:
        n = repo.seed_member_universe(df.to_dicts(), replace=replace)
    typer.secho(f"Seeded {n} member(s) into '{settings.db_schema}'.", fg=typer.colors.GREEN)


@app.command(name="validate-config")
def validate_config() -> None:
    """Load and cross-validate all configuration. Exit non-zero on any error."""
    settings = _bootstrap()
    try:
        config = load_config(settings.config_dir)
    except ConfigError as exc:
        typer.secho(f"INVALID:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    typer.secho(
        f"OK — {len(config.providers)} provider(s), "
        f"{len(config.global_config.matching.matchers)} matcher(s), "
        f"{len(config.global_config.dq_rules)} DQ rule(s).",
        fg=typer.colors.GREEN,
    )


@app.command(name="list-providers")
def list_providers() -> None:
    """List configured providers."""
    settings = _bootstrap()
    try:
        config = load_config(settings.config_dir)
    except ConfigError as exc:
        typer.secho(f"Config error:\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(2) from exc
    for pid, prov in sorted(config.providers.items()):
        typer.echo(f"  {pid}: {prov.display_name} [{prov.format.value}] ({prov.file_glob})")


def _main() -> None:
    app()


if __name__ == "__main__":
    sys.exit(_main())  # type: ignore[func-returns-value]
