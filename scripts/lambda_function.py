"""Lambda function — S3 upload → ECS Fargate matchbot run.

Triggered by EventBridge when any file is uploaded to:
  s3://rilds/data/input/<provider_folder>/<filename>

The function:
  1. Extracts the S3 key from the event
  2. Maps the folder name to a provider_id
  3. Skips files that don't match the provider's file_glob
  4. Starts an ECS Fargate task with the correct --provider and --input

Environment variables (set in Lambda configuration):
  ECS_CLUSTER          matchbot-cluster
  ECS_TASK_DEFINITION  matchbot-task
  ECS_CONTAINER_NAME   matchbot
  ECS_SUBNET_ID        subnet-xxxxxxxxxxxxxxxxx
  ECS_SECURITY_GROUP   sg-xxxxxxxxxxxxxxxxx
  S3_BUCKET            rilds
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

    # --- Read ECS config from environment -----------------------------------
    cluster        = os.environ["ECS_CLUSTER"]
    task_def       = os.environ["ECS_TASK_DEFINITION"]
    container_name = os.environ["ECS_CONTAINER_NAME"]
    subnet_id      = os.environ["ECS_SUBNET_ID"]
    security_group = os.environ["ECS_SECURITY_GROUP"]

    # --- Launch ECS Fargate task --------------------------------------------
    ecs = boto3.client("ecs")

    logger.info(
        "Launching ECS task: provider=%s input=%s", provider_id, s3_uri
    )

    response = ecs.run_task(
        cluster=cluster,
        taskDefinition=task_def,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": [subnet_id],
                "securityGroups": [security_group],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": container_name,
                    "command": [
                        "run",
                        "--provider", provider_id,
                        "--input",   s3_uri,
                    ],
                }
            ]
        },
    )

    tasks = response.get("tasks", [])
    failures = response.get("failures", [])

    if failures:
        logger.error("ECS run_task failures: %s", failures)
        return {"statusCode": 500, "body": f"ECS failures: {failures}"}

    task_arn = tasks[0]["taskArn"] if tasks else "unknown"
    logger.info("ECS task started: %s", task_arn)

    return {
        "statusCode": 200,
        "body": json.dumps({
            "provider":  provider_id,
            "input":     s3_uri,
            "task_arn":  task_arn,
        }),
    }
