# Running MatchBot on AWS ECS Fargate

Unlike the [Glue implementation](glue-implementation.md), which downloads and
installs the package at runtime, the ECS path bakes MatchBot into a **container
image** ahead of time. The trigger is event-driven: a file lands in S3, and
that alone kicks off a run — no manual job launch, no console clicking per
run.

Same core pipeline as everywhere else (`Orchestrator`, same matchers, same
Postgres repository) — only the packaging and invocation differ.

## How the pieces fit together

```
S3 upload (data/input/<provider>/<file>.csv)
        │
        ▼ (EventBridge notification)
Lambda (scripts/lambda_function.py)
        │  maps folder → provider_id, checks file_glob
        ▼ (ecs.run_task)
ECS Fargate Task  ──runs container──▶  matchbot run --provider ... --input s3://...
        │
        ▼
RDS Postgres  (same matchbot.storage.postgres repository as Glue/local)
```

| Piece | File | Role |
|---|---|---|
| Trigger | S3 → EventBridge → Lambda | Detects new provider files, launches the task. |
| Lambda function | [`scripts/lambda_function.py`](../scripts/lambda_function.py) | Maps S3 key → provider, validates filename, calls `ecs.run_task`. |
| Container image | [`Dockerfile`](../Dockerfile) | Bakes the full `matchbot` package + `[aws]` extra + config into a slim image. |
| Runtime adapter | `src/matchbot/runtime/fargate.py` | Tells the orchestrator how to read/write S3 and reach Postgres, for `MATCHBOT_RUNTIME=fargate`. |
| ECS Task Definition | (created in console/CLI, not in repo) | References the image, sets CPU/memory, injects env vars, defines the container name Lambda overrides. |

Compare to Glue: there, the *job script* is fetched fresh from S3 and installs
the wheel every run. Here, the *image* already contains everything — nothing
is downloaded at task start except the input CSV itself. That makes ECS
startup faster per run but means **you must rebuild and push the image**
whenever code or config changes (see below), rather than just re-uploading a
wheel.

## The container image

```dockerfile
FROM python:3.13-slim AS build
...
RUN uv sync --frozen --no-install-project --extra aws   # deps layer, cached
COPY src ./src
COPY config ./config
RUN uv sync --frozen --extra aws                         # install matchbot itself

FROM python:3.13-slim AS runtime
COPY --from=build --chown=matchbot:matchbot /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    MATCHBOT_RUNTIME=fargate \
    MATCHBOT_LOG_JSON=true \
    MATCHBOT_CONFIG_DIR=/app/config
USER matchbot
ENTRYPOINT ["matchbot"]
CMD ["--help"]
```

Key points:
- **Multi-stage build** — the `build` stage installs dependencies and the
  project via `uv sync`; only the resulting `/app` (venv + code + config) is
  copied into the slim `runtime` stage, keeping the final image small (no
  build toolchain, no `uv` binary, no pip cache).
- **`config/` is baked into the image** at `/app/config` — unlike Glue, which
  fetches config fresh from S3 on every run, ECS's config is whatever was
  present at `docker build` time. Changing a provider YAML means rebuilding
  and pushing a new image, not just re-uploading a file.
- **`MATCHBOT_RUNTIME=fargate`** is set as a build-time default — this is
  what makes `get_runtime()` return `FargateRuntime`, wiring in `S3FileSystem`
  + the Postgres repository.
- **`ENTRYPOINT ["matchbot"]`** — the image runs the same Typer CLI
  (`src/matchbot/cli.py`) used locally. Lambda's `containerOverrides.command`
  supplies the subcommand and arguments (`["run", "--provider", ..., "--input", ...]`),
  which get appended after the entrypoint — effectively
  `matchbot run --provider ride_enrollment --input s3://...`.
- **Non-root user** (`matchbot`, uid 10001) — standard container hardening,
  unrelated to MatchBot logic.

### Building and pushing

```bash
# Build
docker build -t matchbot:latest .

# Tag for ECR and push
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com
docker tag matchbot:latest <account-id>.dkr.ecr.<region>.amazonaws.com/matchbot:latest
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/matchbot:latest
```

After pushing a new image, update the **ECS Task Definition** to point at the
new image tag (or use `:latest` and force a new task definition revision) —
`run_task` in Lambda always launches whatever the task definition currently
points to.

## The Lambda trigger

[`scripts/lambda_function.py`](../scripts/lambda_function.py) is the glue
between "a file showed up in S3" and "a matching run happens." It does no
matching itself — it only decides *whether* and *how* to launch a task.

1. **EventBridge event parsing** — expects an S3 `Object Created` event
   (`detail.bucket.name`, `detail.object.key`), which EventBridge delivers
   when S3 → EventBridge notifications are enabled on the bucket.

2. **Key structure check** — expects
   `data/input/<provider_folder>/<filename>`. Anything with fewer than 3
   path segments is skipped (not an error — just logged and ignored, e.g. a
   file dropped directly in `data/input/`).

3. **Folder → provider mapping** (`FOLDER_TO_PROVIDER`) — the S3 folder name
   doesn't have to equal the provider_id, though today it does for all four
   configured providers (`ride_enrollment`, `dlt_ui`, `bhddh`, `dcyf`). This
   indirection exists so a folder can be renamed without touching
   `config/providers/*.yaml`, or vice versa. **Adding a new provider requires
   editing this dict** — it is not config-driven the way `config/providers/`
   is; it's a deploy-time change to the Lambda source.

4. **Filename glob check** (`PROVIDER_GLOB`) — a second guard so that, e.g.,
   uploading `ride_enrollment_notes.txt` into the right folder doesn't
   trigger a run. This must be kept in sync with `file_glob` in the
   provider's YAML — the Lambda has its own copy rather than reading the
   YAML, since Lambda has no access to `config/` at runtime.

5. **`ecs.run_task`** — launches one Fargate task per matching upload, with:
   - `networkConfiguration.awsvpcConfiguration` — subnet + security group
     from Lambda's own env vars (`ECS_SUBNET_ID`, `ECS_SECURITY_GROUP`),
     `assignPublicIp: ENABLED` (task needs outbound internet access to reach
     S3/RDS unless a NAT gateway or VPC endpoints are configured instead).
   - `containerOverrides` — replaces the image's default `CMD ["--help"]`
     with `["run", "--provider", provider_id, "--input", s3_uri]`, addressed
     to the container name from `ECS_CONTAINER_NAME` (must match the name in
     the task definition, not just the image).

6. Returns `500` with the ECS failure list if `run_task` reports failures
   (e.g. insufficient capacity, bad subnet), `200` on success with the
   started task's ARN — visible in the Lambda's own CloudWatch log group for
   debugging trigger-level issues separately from task-level ones.

### Required Lambda environment variables

| Variable | Example | Purpose |
|---|---|---|
| `ECS_CLUSTER` | `matchbot-cluster` | Which ECS cluster to launch into |
| `ECS_TASK_DEFINITION` | `matchbot-task` | Which task definition (and thus image) to run |
| `ECS_CONTAINER_NAME` | `matchbot` | Must match the container name inside the task definition |
| `ECS_SUBNET_ID` | `subnet-xxxxxxxxxxxxxxxxx` | VPC subnet for the task's ENI |
| `ECS_SECURITY_GROUP` | `sg-xxxxxxxxxxxxxxxxx` | Security group for the task's ENI |
| `S3_BUCKET` | `rilds` | Referenced for documentation; not read in code today |

Database connection details (`DATABASE_URL`, `DB_SCHEMA`) are **not** passed
through Lambda — they belong to the ECS **task definition's** own environment
variables (or Secrets Manager reference), since the task, not the Lambda,
is what connects to Postgres.

## `FargateRuntime`

```python
# src/matchbot/runtime/fargate.py
class FargateRuntime(Runtime):
    name = "fargate"

    def filesystem(self) -> FileSystem:
        return S3FileSystem()          # boto3, lazily imported

    def repository(self, settings: Settings) -> Repository:
        return make_repository(settings)   # same Postgres repository as Glue/local
```

Selected via `MATCHBOT_RUNTIME=fargate` (baked into the image as a default,
per the Dockerfile). `S3FileSystem` is the same class Glue's `GlueRuntime`
reuses — this is the one adapter shared verbatim between the two runtimes.

## Networking prerequisites

Same underlying requirement as Glue, expressed differently because ECS
launches its own ENI per task rather than going through a Glue Connection:

- The **task's security group** (`ECS_SECURITY_GROUP`) needs outbound access
  to RDS (port 5432) and to S3 (443) — either via `assignPublicIp: ENABLED`
  plus an internet/NAT gateway, or via VPC endpoints if the subnet is
  private-only.
- **RDS's security group** needs an inbound rule allowing port 5432 from the
  ECS task's security group — the same kind of rule Glue needed, just
  pointed at a different source security group.
- Unlike Glue, ECS does **not** need the "self-referencing all-traffic"
  rule — that requirement is specific to Glue's internal Spark-cluster ENI
  communication, which ECS Fargate tasks don't have (each task is a single
  container, not a driver+executors cluster).

## Typical workflow for a code/config change

1. Edit `src/matchbot/` and/or `config/*.yaml`.
2. Rebuild and push the image (see "Building and pushing" above).
3. Update the ECS task definition to the new image (new revision), or ensure
   the service/task launch always pulls `:latest` fresh.
4. Upload a test file to `s3://rilds/data/input/<provider>/` — this alone
   triggers Lambda → ECS, no console interaction needed for the run itself.
5. Check CloudWatch Logs — Lambda's log group shows whether the task was
   launched (or why it wasn't); the ECS task's own log group (via the task
   definition's `awslogs` driver, not shown in this repo's files) shows the
   actual `matchbot run` output — the same per-file match summary line
   (`rows_matched/rows_staged`, match rate, unmatched, ambiguous) as Glue and
   local runs.

## Where this differs from Glue, at a glance

| | ECS Fargate | Glue |
|---|---|---|
| Code delivery | Baked into container image at build time | Wheel downloaded + `pip install`ed at job start |
| Config delivery | Baked into image at `/app/config` | Downloaded fresh from S3 every run |
| Trigger | Automatic (S3 → EventBridge → Lambda → `run_task`) | Manual run, or a scheduler/trigger you configure separately |
| To pick up a code change | Rebuild + push image, update task definition | Rebuild wheel, re-upload to S3 — job picks it up next run automatically |
| Networking quirk | RDS inbound rule from task's SG | RDS inbound rule *and* Glue SG self-reference rule |
| Runtime adapter | `FargateRuntime` (`S3FileSystem` + Postgres repo) | `GlueRuntime` (same `S3FileSystem` + Postgres repo, reused) |
