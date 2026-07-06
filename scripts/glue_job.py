"""AWS Glue job entry point for matchbot.

Glue passes job arguments via --job-name and custom --key=value pairs.
This script reads those args, installs the matchbot wheel at runtime,
configures the environment, then delegates to the same pipeline logic
used by the Fargate and local runtimes.

Supported commands (passed as --command):
  init-db       — create schema + tables in RDS
  seed-members  — load member_universe from S3 CSV
  run           — run the full matching pipeline

Required Glue job parameters:
  --command         init-db | seed-members | run
  --database_url    postgresql://user:pass@host:5432/dbname
  --db_schema       public
  --config_s3_uri   s3://bucket/config/
  --wheel_s3_uri    s3://bucket/glue/wheels/matchbot-0.1.0-py3-none-any.whl

For seed-members:
  --input_uri       s3://bucket/member_universe.csv

For run:
  --provider        ride_enrollment
  --input_uri       s3://bucket/input/ride_enrollment/
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


def _get_args() -> dict[str, str]:
    args: dict[str, str] = {}
    i = 0
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg.startswith("--") and i + 1 < len(sys.argv):
            key = arg.lstrip("-").replace("-", "_")
            args[key] = sys.argv[i + 1]
            i += 2
        else:
            i += 1
    return args


def _download_from_s3(s3_uri: str, local_path: str) -> None:
    import boto3
    from urllib.parse import urlparse
    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    boto3.client("s3").download_file(bucket, key, local_path)


def _sync_config_from_s3(s3_uri: str, local_dir: Path) -> None:
    import boto3
    from urllib.parse import urlparse
    parsed = urlparse(s3_uri.rstrip("/"))
    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/")
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith("/"):
                continue  # S3 "folder" placeholder object, not a real file
            relative = key[len(prefix):].lstrip("/")
            if not relative:
                continue
            dest = local_dir / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            s3.download_file(bucket, key, str(dest))


def _install_wheel(wheel_s3_uri: str) -> None:
    """Download wheel from S3 and install it with pip."""
    # pip validates the wheel filename against PEP 427 (name-version-...-platform.whl),
    # so the local path must keep the original filename — a renamed "-latest.whl"
    # is rejected as "not a valid wheel filename" before install is even attempted.
    wheel_filename = wheel_s3_uri.rsplit("/", 1)[-1]
    tmp_wheel = f"/tmp/{wheel_filename}"
    print(f"Downloading wheel from {wheel_s3_uri}")
    _download_from_s3(wheel_s3_uri, tmp_wheel)
    print("Installing wheel and dependencies...")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", f"{tmp_wheel}[aws]"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"pip install failed:\n{result.stderr}")
        sys.exit(1)
    print("Wheel installed.")


def main() -> None:
    args = _get_args()

    command = args.get("command", "")
    if command not in ("init-db", "seed-members", "run"):
        print(f"ERROR: --command must be init-db | seed-members | run, got: {command!r}")
        sys.exit(1)

    database_url = args.get("database_url", "")
    if not database_url:
        print("ERROR: --database_url is required")
        sys.exit(1)

    db_schema = args.get("db_schema", "public")
    config_s3_uri = args.get("config_s3_uri", "")
    if not config_s3_uri:
        print("ERROR: --config_s3_uri is required")
        sys.exit(1)

    wheel_s3_uri = args.get("wheel_s3_uri", "")
    if not wheel_s3_uri:
        print("ERROR: --wheel_s3_uri is required")
        sys.exit(1)

    # Install the wheel first (--no-deps so it doesn't conflict with Glue's pip)
    _install_wheel(wheel_s3_uri)

    # Download config from S3
    tmp_config = Path(tempfile.mkdtemp(prefix="matchbot_config_"))
    print(f"Downloading config from {config_s3_uri} to {tmp_config}")
    _sync_config_from_s3(config_s3_uri, tmp_config)

    # Set environment variables
    os.environ["DATABASE_URL"] = database_url
    os.environ["DB_SCHEMA"] = db_schema
    os.environ["MATCHBOT_RUNTIME"] = "glue"
    os.environ["MATCHBOT_CONFIG_DIR"] = str(tmp_config)
    os.environ["MATCHBOT_LOG_JSON"] = "true"
    os.environ["MATCHBOT_LOG_LEVEL"] = args.get("log_level", "INFO")

    # Optional: email a run-completion summary via SES instead of just
    # logging it. Pass --notifier ses --ses_sender ... --ses_recipients ...
    # as Glue job parameters to enable; defaults to log-only, unchanged.
    if "notifier" in args:
        os.environ["MATCHBOT_NOTIFIER"] = args["notifier"]
    if "ses_sender" in args:
        os.environ["MATCHBOT_SES_SENDER"] = args["ses_sender"]
    if "ses_recipients" in args:
        os.environ["MATCHBOT_SES_RECIPIENTS"] = args["ses_recipients"]

    from matchbot.config.settings import get_settings
    from matchbot.logging_setup import configure_logging
    from matchbot.runtime.factory import get_runtime

    settings = get_settings()
    configure_logging(level=settings.log_level, json_logs=settings.log_json)
    runtime = get_runtime(settings.runtime)

    if command == "init-db":
        print("Running: init-db")
        with runtime.repository(settings) as repo:
            repo.init_schema()
        print(f"Schema '{db_schema}' initialized.")

    elif command == "seed-members":
        import io
        import polars as pl
        input_uri = args.get("input_uri", "")
        if not input_uri:
            print("ERROR: --input_uri is required for seed-members")
            sys.exit(1)
        print(f"Running: seed-members from {input_uri}")
        fs = runtime.filesystem()
        csv_bytes = fs.read_bytes(input_uri)
        df = pl.read_csv(io.BytesIO(csv_bytes), infer_schema_length=0)
        with runtime.repository(settings) as repo:
            n = repo.seed_member_universe(df.to_dicts(), replace=True)
        print(f"Seeded {n} members into '{db_schema}'.")

    elif command == "run":
        from matchbot.config.loader import ConfigError, load_config
        from matchbot.notify.factory import get_notifier
        from matchbot.pipeline.orchestrator import Orchestrator

        provider = args.get("provider", "")
        input_uri = args.get("input_uri", "")
        if not provider or not input_uri:
            print("ERROR: --provider and --input_uri are required for run")
            sys.exit(1)
        print(f"Running: match pipeline for provider={provider} input={input_uri}")
        try:
            config = load_config(settings.config_dir)
        except ConfigError as exc:
            print(f"Config error: {exc}")
            sys.exit(2)

        fs = runtime.filesystem()
        notifier = get_notifier(settings)
        with runtime.repository(settings) as repo:
            orch = Orchestrator(config, settings, repo, fs, notifier)
            results = orch.run_provider(provider, input_uri)

        for r in results:
            m = r.metrics
            print(
                f"{r.source_uri}: {m.rows_matched}/{m.rows_staged} matched "
                f"({m.match_rate:.1%}), {m.rows_unmatched} unmatched "
                f"[{m.status.value}]"
            )
        failures = sum(1 for r in results if r.metrics.status.value != "success")
        if failures:
            sys.exit(1)


if __name__ == "__main__":
    main()
