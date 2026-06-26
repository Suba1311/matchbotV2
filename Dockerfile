# MatchBot V2 — Fargate task image.
# Multi-stage: build a venv with uv, then copy into a slim runtime image.

FROM python:3.13-slim AS build

# uv for fast, reproducible installs from the lockfile.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install dependencies first (cached layer), then the project + the [aws] extra
# so the Fargate runtime can reach S3/SES.
COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --extra aws

COPY src ./src
COPY config ./config
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --extra aws


FROM python:3.13-slim AS runtime

# Non-root for safety.
RUN useradd --create-home --uid 10001 matchbot
WORKDIR /app

COPY --from=build --chown=matchbot:matchbot /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    MATCHBOT_RUNTIME=fargate \
    MATCHBOT_LOG_JSON=true \
    MATCHBOT_CONFIG_DIR=/app/config

USER matchbot

# The container's job is one orchestrated run. EventBridge/ECS passes the
# provider + S3 input as the command, e.g.:
#   matchbot run --provider provider2_unemployment --input s3://bucket/dropzone/
ENTRYPOINT ["matchbot"]
CMD ["--help"]
