"""Lambda function — S3 upload → AWS Glue matchbot run.

Triggered by EventBridge when any file is uploaded to:
  s3://rilds/data/input/<provider_folder>/<filename>

The function:
  1. Extracts the S3 key from the event
  2. Maps the folder name to a provider_id
  3. Skips files that don't match the provider's file_glob
  4. Starts a Glue job run with --provider and --input_uri set for this file

Independent of scripts/lambda_function.py (the ECS trigger) — the two can run
side by side, e.g. while comparing ECS and Glue output during a migration.

Environment variables (set in Lambda configuration):
  GLUE_JOB_NAME     matchbot-run
  DATABASE_URL      postgresql://user:pass@host:5432/dbname
  DB_SCHEMA         rilds
  CONFIG_S3_URI     s3://rilds/glue/config/
  WHEEL_S3_URI      s3://rilds/glue/wheels/matchbot-0.1.0-py3-none-any.whl
"""

from __future__ import annotations

import fnmatch
import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Folder name → provider_id mapping.
# Add a new entry here when onboarding a new provider.
# Key   = the S3 folder name under data/input/
# Value = the provider_id in config/providers/*.yaml
#
# Kept identical to scripts/lambda_function.py — update both when onboarding
# a new provider, since Lambda has no access to config/ at runtime.
# ---------------------------------------------------------------------------
FOLDER_TO_PROVIDER: dict[str, str] = {
    "ride_enrollment": "ride_enrollment",
    "dlt_ui":          "dlt_ui",
    "bhddh":           "bhddh",
    "dcyf":            "dcyf",
}

# ---------------------------------------------------------------------------
# File glob per provider — skip files that don't match.
# Must match file_glob in the provider's YAML config.
# ---------------------------------------------------------------------------
PROVIDER_GLOB: dict[str, str] = {
    "ride_enrollment": "ride_enrollment_*.csv",
    "dlt_ui":          "dlt_ui_*.csv",
    "bhddh":           "bhddh_*.csv",
    "dcyf":            "dcyf_*.csv",
}


def lambda_handler(event: dict, context: object) -> dict:
    logger.info("Event received: %s", json.dumps(event))

    # --- Extract S3 details from EventBridge event --------------------------
    detail = event.get("detail", {})
    bucket = detail.get("bucket", {}).get("name", "")
    key = detail.get("object", {}).get("key", "")

    if not bucket or not key:
        logger.error("Missing bucket or key in event: %s", event)
        return {"statusCode": 400, "body": "Missing bucket or key"}

    s3_uri = f"s3://{bucket}/{key}"
    filename = key.rsplit("/", 1)[-1]

    # key format: data/input/<provider_folder>/<filename>
    parts = key.split("/")
    if len(parts) < 3:
        logger.info("Skipping — key does not match expected structure: %s", key)
        return {"statusCode": 200, "body": "Skipped — unexpected key structure"}

    folder = parts[-2]  # e.g. "ride_enrollment"

    # --- Map folder to provider ---------------------------------------------
    provider_id = FOLDER_TO_PROVIDER.get(folder)
    if not provider_id:
        logger.info("Skipping — no provider configured for folder: %s", folder)
        return {"statusCode": 200, "body": f"Skipped — unknown folder {folder!r}"}

    # --- Check file matches provider glob -----------------------------------
    glob = PROVIDER_GLOB.get(provider_id, "*.csv")
    if not fnmatch.fnmatch(filename, glob):
        logger.info(
            "Skipping — filename %r does not match glob %r for provider %r",
            filename, glob, provider_id,
        )
        return {"statusCode": 200, "body": f"Skipped — {filename!r} does not match {glob!r}"}

    # --- Read Glue config from environment -----------------------------------
    job_name      = os.environ["GLUE_JOB_NAME"]
    database_url  = os.environ["DATABASE_URL"]
    db_schema     = os.environ["DB_SCHEMA"]
    config_s3_uri = os.environ["CONFIG_S3_URI"]
    wheel_s3_uri  = os.environ["WHEEL_S3_URI"]

    # --- Start the Glue job run ----------------------------------------------
    glue = boto3.client("glue")

    logger.info(
        "Starting Glue job %r: provider=%s input=%s", job_name, provider_id, s3_uri
    )

    try:
        response = glue.start_job_run(
            JobName=job_name,
            Arguments={
                "--command": "run",
                "--database_url": database_url,
                "--db_schema": db_schema,
                "--config_s3_uri": config_s3_uri,
                "--wheel_s3_uri": wheel_s3_uri,
                "--provider": provider_id,
                "--input_uri": s3_uri,
            },
        )
    except Exception as exc:  # noqa: BLE001 - surface any boto3/Glue error as a 500
        logger.error("Glue start_job_run failed: %s", exc)
        return {"statusCode": 500, "body": f"Glue start_job_run failed: {exc}"}

    job_run_id = response.get("JobRunId", "unknown")
    logger.info("Glue job run started: %s", job_run_id)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "provider":   provider_id,
            "input":      s3_uri,
            "job_name":   job_name,
            "job_run_id": job_run_id,
        }),
    }
