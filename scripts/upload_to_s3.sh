#!/usr/bin/env bash
# Upload everything Glue needs to S3.
# Usage: ./scripts/upload_to_s3.sh <s3-bucket-name>
# Example: ./scripts/upload_to_s3.sh matchbot-dropzone-riopc

set -euo pipefail

BUCKET="${1:?Usage: $0 <bucket-name>}"
PREFIX="glue"

echo "==> Building matchbot wheel..."
uv build --wheel
WHEEL=$(ls dist/matchbot-*.whl | tail -1)
echo "    Built: $WHEEL"

WHEEL_NAME=$(basename "$WHEEL")

echo "==> Uploading wheel..."
aws s3 cp "$WHEEL" "s3://$BUCKET/$PREFIX/wheels/$WHEEL_NAME"

echo "==> Uploading Glue entry script..."
aws s3 cp scripts/glue_job.py "s3://$BUCKET/$PREFIX/glue_job.py"

echo "==> Uploading config..."
aws s3 sync config/ "s3://$BUCKET/$PREFIX/config/" --delete

echo "==> Uploading sample data..."
aws s3 cp data/samples/ride_enrollment_1k.csv \
    "s3://$BUCKET/data/input/ride_enrollment/ride_enrollment_1k.csv"

echo ""
echo "Done. S3 layout:"
echo "  s3://$BUCKET/$PREFIX/wheels/$WHEEL_NAME           <- wheel"
echo "  s3://$BUCKET/$PREFIX/glue_job.py                  <- entry script"
echo "  s3://$BUCKET/$PREFIX/config/                      <- config folder"
echo "  s3://$BUCKET/data/input/ride_enrollment/          <- enrollment input"
echo ""
echo "NOTE: update the Glue job's --wheel_s3_uri parameter (and the Glue"
echo "trigger Lambda's WHEEL_S3_URI env var) to match the wheel filename above"
echo "if the version changed."
