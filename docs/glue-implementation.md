# Running MatchBot on AWS Glue

MatchBot's core pipeline (`src/matchbot/pipeline/orchestrator.py`) is plain
Python/Polars — it has no Spark dependency. On Glue this is used as "managed
Python on a schedule": Glue provisions compute, downloads a script, and runs
it. The script installs the same MatchBot package used by Fargate and local
runs, then calls the same `Orchestrator`. No matching logic is duplicated or
reimplemented for Glue.

This mirrors how MatchBot already runs on ECS Fargate — same config, same
Postgres repository, same matchers — just packaged and invoked differently.

## How the pieces fit together

```
S3 (wheel + config + input CSV)
        │
        ▼
Glue Job  ──runs──▶  scripts/glue_job.py  ──installs & imports──▶  matchbot package
        │                                                                │
        └── RDS Postgres  ◀── matchbot.storage.postgres (same as Fargate) ┘
```

| Piece | File | Role |
|---|---|---|
| Job entrypoint | [`scripts/glue_job.py`](../scripts/glue_job.py) | What Glue actually executes. Bootstraps the environment, then delegates to MatchBot. |
| Runtime adapter | [`src/matchbot/runtime/glue.py`](../src/matchbot/runtime/glue.py) | Tells the orchestrator how to read/write files (S3) and reach the DB (Postgres), for `MATCHBOT_RUNTIME=glue`. |
| Packaged code | wheel (`matchbot-<version>-py3-none-any.whl`) | The entire `src/matchbot/` tree + dependencies, built locally and uploaded to S3. Glue has no other way to see this code. |
| Config | `config/global.yaml`, `config/providers/*.yaml` | Uploaded to S3 alongside the wheel; downloaded fresh on every job run. |

## Why a wheel file is involved

A `.whl` is Python's standard pre-built package format — a zip archive of the
package code plus metadata (name, version, dependencies from
`pyproject.toml`). It's the same artifact `pip install <package>` would give
you from PyPI.

Glue jobs start in a fresh, empty, AWS-managed Python environment on every
run. It has no knowledge of MatchBot. The wheel is how the code gets there:

```bash
uv build --wheel   # produces dist/matchbot-<version>-py3-none-any.whl
```

Upload the result to S3 (e.g. `s3://rilds/glue/wheels/`). `glue_job.py`
downloads it and runs `pip install <wheel>[aws]` before anything else — only
after that succeeds do the `matchbot.*` imports later in the script become
possible.

**Rebuild and re-upload the wheel every time `src/matchbot/` changes.** The
Glue job always pulls whatever is at the fixed S3 key; a stale wheel silently
runs old code with no warning.

## `GlueRuntime`

```python
# src/matchbot/runtime/glue.py
class GlueRuntime(Runtime):
    name = "glue"

    def filesystem(self) -> FileSystem:
        return S3FileSystem()          # same S3 adapter Fargate uses

    def repository(self, settings: Settings) -> Repository:
        return make_repository(settings)   # same Postgres repository Fargate uses
```

Selected via the `MATCHBOT_RUNTIME=glue` environment variable through
`matchbot.runtime.factory.get_runtime()`. Functionally identical to
`FargateRuntime` today — the only reason it's a separate class is to keep the
door open for a Glue-specific optimization later (e.g. Spark-native S3/JDBC
readers) without touching Fargate's code path.

## `scripts/glue_job.py` walkthrough

This script is **not** part of the wheel — Glue downloads and runs it
directly as the job's `ScriptLocation`. Its only job is to bootstrap the
environment, then get out of the way.

1. **`_get_args()`** — Glue passes job parameters as `--key value` pairs on
   the command line. This does its own minimal parsing (no `argparse`/Typer,
   since Glue's own bootstrapping adds extra positional args that a stricter
   parser would choke on).

2. **`_install_wheel()`** — downloads the wheel from `--wheel_s3_uri` to
   `/tmp/`, keeping its original filename (pip validates wheel filenames
   against [PEP 427](https://peps.python.org/pep-0427/) — a renamed
   `matchbot-latest.whl` is rejected as invalid before install is even
   attempted). Installs with the `[aws]` extra so `boto3` (needed by
   `S3FileSystem`) is present.

3. **`_sync_config_from_s3()`** — downloads `config/global.yaml` and
   `config/providers/*.yaml` from `--config_s3_uri` into a temp directory.
   Skips any S3 key ending in `/` — these are zero-byte "folder placeholder"
   objects some upload tools create, not real files; downloading one would
   collide with the real `providers/` directory already created for the
   actual provider YAMLs.

4. Sets environment variables (`DATABASE_URL`, `DB_SCHEMA`,
   `MATCHBOT_RUNTIME=glue`, `MATCHBOT_CONFIG_DIR`, log settings) so
   `matchbot.config.settings.get_settings()` picks them up exactly as it
   would from a `.env` file locally or task-definition env vars on ECS.

5. Dispatches on `--command`:
   - `init-db` → `repo.init_schema()` — same idempotent schema/table creation used everywhere else.
   - `seed-members` → reads a CSV via `S3FileSystem`, loads it into the Member Universe.
   - `run` → loads config, builds an `Orchestrator`, calls `run_provider(provider, input_uri)` — **this is the actual matching pipeline**, identical to `matchbot run --provider ... --input ...` on ECS/local.

6. Prints a per-file summary line (rows matched/staged, match rate, unmatched,
   ambiguous, status) and exits non-zero if any file failed — visible in the
   job's **Output logs** in CloudWatch.

## Job parameters reference

| Parameter | Required for | Example |
|---|---|---|
| `--command` | always | `run` |
| `--database_url` | always | `postgresql://user:pass@host:5432/matchbot` |
| `--db_schema` | always | `rilds` |
| `--config_s3_uri` | always | `s3://rilds/glue/config/` |
| `--wheel_s3_uri` | always | `s3://rilds/glue/wheels/matchbot-0.1.0-py3-none-any.whl` |
| `--provider` | `run` | `ride_enrollment` |
| `--input_uri` | `run`, `seed-members` | `s3://rilds/data/input/ride_enrollment/ride_enrollment_1k.csv` |
| `--log_level` | optional | `INFO` |

## Networking prerequisites

Glue jobs attached to a VPC connection (required to reach RDS) provision
network interfaces that must be able to talk to each other:

- The security group attached to the Glue connection needs a **self-referencing
  inbound rule allowing all traffic** (source = itself) — this is an AWS
  requirement for Glue's internal ENI-to-ENI communication, not related to RDS
  access. Without it, the job fails immediately with
  `InvalidInputException: At least one security group must open all ingress ports.`
- Separately, RDS's own security group needs an inbound rule allowing port
  5432 from the Glue job's security group.

## Common failure modes hit while setting this up

| Symptom | Cause | Fix |
|---|---|---|
| `InvalidInputException: At least one security group must open all ingress ports` | Glue connection's security group has no self-referencing all-traffic rule | Add inbound rule: all traffic, source = the security group itself |
| `pip install failed: ... is not a valid wheel filename` | Local download path renamed the wheel (e.g. `matchbot-latest.whl`) | Keep the original filename from the S3 key when downloading |
| `ModuleNotFoundError: No module named 'yaml'` (or similar) | Wheel installed with `--no-deps`, so MatchBot's actual dependencies never landed | Drop `--no-deps`; install `<wheel>[aws]` so dependencies + boto3 are pulled in |
| `Providers directory not found` | `config/providers/` never made it to S3, or the sync silently downloaded nothing | Verify `s3://.../config/` mirrors the local `config/` tree exactly (`global.yaml` + `providers/*.yaml`) |
| `FileExistsError: ... 'providers'` | S3 "folder" placeholder object (zero-byte key ending in `/`) collided with the real directory | Skip any S3 key ending in `/` during config sync |
| `OperationalError: failed to resolve host ...` | Typo/duplication in the `--database_url` job parameter's hostname | Copy the exact endpoint from RDS console → Connectivity & security |

## Typical console workflow for a run

1. Build and upload a fresh wheel whenever `src/matchbot/` changes:
   ```bash
   uv build --wheel
   ```
   Upload `dist/matchbot-<version>-py3-none-any.whl` to
   `s3://rilds/glue/wheels/` via the S3 console.
2. Keep `s3://rilds/glue/config/` in sync with the local `config/` directory
   whenever YAML changes.
3. Upload provider input files to `s3://rilds/data/input/<provider>/`.
4. **Glue → ETL jobs → matchbot-run → Run with parameters**, setting
   `--provider` and `--input_uri` for this run.
5. **Runs tab → (this run) → Output logs** for the match summary;
   **Error logs** for tracebacks.
