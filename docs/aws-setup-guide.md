# MatchBot — AWS Glue and ECS Setup Guide

This is a step-by-step console guide for setting up and running MatchBot on
both AWS Glue and AWS ECS Fargate, including the automated S3-upload triggers
and the issues encountered (and fixed) while standing this up. For an
explanation of *why* the code is structured the way it is, see
[glue-implementation.md](glue-implementation.md) and
[ecs-implementation.md](ecs-implementation.md).

---

## Part 1 — Shared prerequisites

Both platforms need the same underlying pieces:

- An S3 bucket (`rilds` in this setup) holding provider input files, config,
  and (for Glue) the packaged wheel
- An RDS Postgres instance with the MatchBot schema initialized
  (`matchbot init-db`)
- Network connectivity from the compute (Glue job / ECS task) to RDS

### 1.1 Build the deployable Python package (wheel)

Both the Glue path and the initial ECS approach rely on a built wheel at some
point. On your local machine, in the project directory:

```bash
uv build --wheel
```

This produces `dist/matchbot-0.1.0-py3-none-any.whl`. **Rebuild this any time
`src/matchbot/` changes** — an existing `.whl` file is not automatically
regenerated; forgetting this step was the cause of several "fix didn't work"
incidents during setup (see Part 4).

Verify the fix you expect is actually in the built wheel before uploading:
```bash
unzip -p dist/matchbot-0.1.0-py3-none-any.whl matchbot/pipeline/orchestrator.py | grep -A5 "some_expected_string"
```

### 1.2 Initialize the RDS schema — without ECS or Glue

`matchbot init-db` creates the schema and tables idempotently (safe to
re-run). This is a one-time (or rarely-repeated) setup step and does not need
a full ECS task or Glue job launch — run it directly against RDS instead.

**Option A — from your local machine (simplest):**

```bash
# In matchbotV2/, with .env pointing at the real RDS instance:
#   DATABASE_URL=postgresql://<user>:<pass>@<rds-endpoint>:5432/<dbname>
#   DB_SCHEMA=<your schema>
uv run matchbot init-db
```

This only works if your machine can reach RDS on port 5432 — same
requirement as connecting via DBeaver or `psql`. If RDS sits in a private VPC
subnet with no public access, your local machine can't reach it unless
you're on a VPN or bastion host with routing into that VPC.

**Option B — AWS CloudShell (browser-based, no local network path needed):**

Useful when RDS has no public access and your local machine can't reach it,
but CloudShell can (note: CloudShell isn't automatically inside your VPC
either — this only helps if CloudShell's network path to RDS is actually
open, e.g. RDS allows the CloudShell environment's egress, or CloudShell is
configured with a VPC environment).

1. Open **CloudShell** from the AWS Console top navigation bar
2. Upload the project (zip it locally, upload via CloudShell's **Actions →
   Upload file**, then `unzip`) — same process used earlier for building the
   ECS image in CloudShell
3. Install `uv` and Python 3.11+ in CloudShell if not already present:
   ```bash
   curl -LsSf https://astral.sh/uv/install.sh | sh
   ```
4. From the project directory:
   ```bash
   uv sync
   uv run matchbot init-db
   ```

Both options call the exact same idempotent schema builder the ECS image and
Glue job use internally — there's no drift risk from doing this outside
those platforms.

---

## Part 2 — AWS Glue setup

### 2.1 Upload artifacts to S3

**S3 console → rilds bucket:**
1. Create/open `glue/wheels/` → Upload → `dist/matchbot-0.1.0-py3-none-any.whl`
2. Create/open `glue/scripts/` → Upload → `scripts/glue_job.py`
3. Create/open `glue/config/` → Upload the entire local `config/` directory,
   preserving structure (`global.yaml` + `providers/*.yaml`)

   **Caution:** if you create folders manually in the S3 console before
   uploading, S3 sometimes creates zero-byte "folder placeholder" objects
   (keys ending in `/`). The code handles this correctly (skips them during
   config sync), but it's cleaner to drag-and-drop a whole local folder
   instead of pre-creating empty folders.

### 2.2 Create an IAM role for Glue

**IAM console → Roles → Create role:**
- Trusted entity: AWS service → Glue
- Attach: `AWSGlueServiceRole` (managed policy)
- Add an inline policy granting `s3:GetObject` / `s3:ListBucket` on your
  bucket, and `s3:PutObject` if the job writes anything back to S3
- Name it e.g. `matchbot-glue-role`

### 2.3 Create a security group for Glue's VPC connection

Glue jobs that connect to RDS need a VPC-attached connection, which requires
a dedicated security group with a specific self-referencing rule.

**EC2 console → Security Groups → Create security group:**
1. Name: `matchbot-glue-sg`
2. VPC: same VPC as your RDS instance
3. Leave inbound rules **empty** for now — create the group first
4. Create the security group

Then, **immediately after creation**, edit it to add the required
self-reference rule:
1. Open `matchbot-glue-sg` → Inbound rules → Edit inbound rules
2. Add rule: Type = All traffic, Source = Custom → search for and select
   `matchbot-glue-sg` itself (only selectable now that the group exists)
3. Save

> **Why this rule is required:** AWS Glue provisions elastic network
> interfaces (ENIs) for Spark driver/executor communication. Glue requires at
> least one attached security group to allow all-traffic ingress from itself,
> or job startup fails immediately with:
> `InvalidInputException: At least one security group must open all ingress ports.`
> This is unrelated to RDS access — it's purely for Glue's internal
> node-to-node communication.

### 2.4 Allow Glue to reach RDS

**EC2 console → Security Groups → (your RDS instance's security group):**
1. Inbound rules → Edit inbound rules → Add rule
2. Type: PostgreSQL (port 5432), Source: Custom → `matchbot-glue-sg`
3. Save

### 2.5 Create a Glue connection

**AWS Glue console → Data connections → Create connection:**
- Type: JDBC
- JDBC URL: `jdbc:postgresql://<rds-endpoint>:5432/<dbname>`
- VPC / Subnet: same as RDS
- Security group: `matchbot-glue-sg`
- Name: `matchbot-rds-connection`

### 2.6 Create the Glue job

**AWS Glue console → ETL jobs → Create job:**

You'll see three cards: **Visual ETL**, **Notebook**, **Script editor** —
choose **Script editor** (Visual ETL only produces Spark jobs and hides the
Python Shell option; if you want Python Shell specifically, it's only
reachable from Script editor's Engine dropdown, and even then may not be
available — see the compatibility note in section 2.6.1).

1. Choose **Upload an existing script** → select `scripts/glue_job.py` (or
   paste its content in afterward)
2. **Job details** tab:
   - Name: `matchbot-run`
   - IAM Role: `matchbot-glue-role`
   - Type/Engine: **Spark** (see 2.6.1 for why Python Shell isn't used here)
   - Glue version: latest (5.x) — Python 3.11 under the hood
   - Worker type: G.1X, Number of workers: 2 (practical minimum for Spark)
3. **Advanced properties → Connections**: attach `matchbot-rds-connection`
4. **Advanced properties → Job parameters**, add:

   | Key | Value |
   |---|---|
   | `--wheel_s3_uri` | `s3://rilds/glue/wheels/matchbot-0.1.0-py3-none-any.whl` |
   | `--config_s3_uri` | `s3://rilds/glue/config/` |
   | `--database_url` | `postgresql://<user>:<pass>@<rds-endpoint>:5432/<dbname>` |
   | `--db_schema` | your schema name |
   | `--command` | `run` |

5. Save

#### 2.6.1 Why Spark, not Python Shell

AWS Glue's Python Shell job type runs a fixed Python 3.9 runtime, and does
**not** appear as an option in the newer Glue Studio "Create job" visual
flow at all — only in Script editor's Engine dropdown, and even there it may
only show Spark/Spark Streaming depending on your console version.

Separately, even if Python Shell is available, MatchBot's dependency
`duckdb>=1.5.4` publishes no `cp39` (Python 3.9) wheel on PyPI — only
`cp310` and up — so `pip install` fails under Python Shell's fixed runtime
regardless. (This was resolved by removing the unused `duckdb` dependency
from `pyproject.toml`, but Spark remains the simpler, already-working choice
given the console limitation.)

Cost implication: Spark jobs have a 2 DPU minimum (~$0.44/DPU-hour), so even
a fast job costs roughly $0.02–0.03 per run regardless of actual duration —
this is a fixed floor, not something reduced by making the job faster.

### 2.7 Run and verify

**Glue console → ETL jobs → matchbot-run → Run with parameters:**
- `--provider` = `ride_enrollment`
- `--input_uri` = `s3://rilds/data/input/ride_enrollment/ride_enrollment_1k.csv`

Check **Runs tab → (this run) → Output logs** for the match summary line;
**Error logs** for tracebacks if it fails.

---

## Part 3 — ECS Fargate setup

### 3.1 Build and push the container image

The Dockerfile bakes the code and config into the image at build time
(unlike Glue, which fetches fresh from S3 every run). Build via CodeBuild (if
connected to your GitHub repo) or locally:

```bash
docker build -t matchbot:latest .
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-2.amazonaws.com
docker tag matchbot:latest <account-id>.dkr.ecr.us-east-2.amazonaws.com/matchbot:latest
docker push <account-id>.dkr.ecr.us-east-2.amazonaws.com/matchbot:latest
```

### 3.2 Create an IAM role for the ECS task

**IAM console → Roles → Create role:**
- Trusted entity: Elastic Container Service → Task
- Needs S3 read access (input files, if reading directly) and RDS
  connectivity (network-level, not IAM)
- Name it e.g. `matchbot-ecs-task-role`

### 3.3 Create the ECS task definition

**ECS console → Task Definitions → Create new task definition:**
- Name: `matchbot-task`
- Launch type: Fargate
- Task CPU: 1 vCPU (1024)
- Task memory: start at **2 GB**, but see Part 4 for known issues at scale —
  8 GB was still insufficient for a 1M-row file before the batching fix
  (Part 4.3) was applied; re-test memory sizing after that fix
- Container:
  - Name: `matchbot` (must match `ECS_CONTAINER_NAME` used by the trigger
    Lambda, below)
  - Image: `<account-id>.dkr.ecr.us-east-2.amazonaws.com/matchbot:latest`
  - Log configuration: `awslogs` driver, a CloudWatch log group of your choice

**Important:** every time you push a new image (same tag), you must
**Create new revision** on this task definition — ECS does not automatically
pick up a new image under an existing revision. This was the root cause of
several "the fix didn't work" false alarms during troubleshooting.

### 3.4 Security groups for ECS → RDS

**EC2 console → Security Groups:**
1. Create (or reuse) a security group for the ECS task, e.g. `matchbot-ecs-sg`
2. On the **RDS security group**: add inbound rule, Type = PostgreSQL (5432),
   Source = `matchbot-ecs-sg`

(ECS does **not** need the Glue-specific self-referencing all-traffic rule —
that requirement is specific to Glue's Spark driver/executor ENI
communication, which a single-container Fargate task doesn't have.)

### 3.5 Run and verify manually (before automating)

Use **ECS console → Clusters → (your cluster) → Run new task**, or test via
the trigger Lambda directly, specifying:
- Task definition: `matchbot-task`
- Container override command: `["run", "--provider", "ride_enrollment", "--input", "s3://rilds/data/input/ride_enrollment/ride_enrollment_1k.csv"]`

Check the task's CloudWatch logs for the match summary line.

---

## Part 4 — Automated S3-upload triggers (both platforms)

Both platforms use the same pattern: **S3 upload → EventBridge → Lambda →
launch compute**. Each platform has its own dedicated Lambda so they can run
independently (e.g. side-by-side during a migration/comparison period).

### 4.1 Enable EventBridge notifications on the bucket

**S3 console → rilds → Properties tab → Amazon EventBridge:**
- Must show **"On"** — this is off by default and is the most commonly
  missed step. If off: Edit → Enable → Save.

### 4.2 Create the EventBridge rule

**EventBridge console → Rules → Create rule:**
- Event source: AWS services → S3
- Event pattern (adjust bucket name/prefix as needed):
  ```json
  {
    "source": ["aws.s3"],
    "detail-type": ["Object Created"],
    "detail": {
      "bucket": { "name": ["rilds"] },
      "object": { "key": [{ "prefix": "data/input/" }] }
    }
  }
  ```
- Targets: add **both** Lambda functions below (or one, if only automating
  one platform)

### 4.3 ECS trigger Lambda

**Lambda console → Create function** → `matchbot-s3-trigger`

Deploy the code from `scripts/lambda_function.py`. It:
1. Parses the S3 key (`data/input/<provider_folder>/<filename>`)
2. Maps the folder name to a `provider_id` (edit `FOLDER_TO_PROVIDER` in the
   script to onboard new providers)
3. Validates the filename against the provider's expected glob
4. Calls `ecs.run_task(...)` with the provider and S3 URI as container
   command overrides

**Environment variables:**

| Key | Example |
|---|---|
| `ECS_CLUSTER` | `matchbot-cluster` |
| `ECS_TASK_DEFINITION` | `matchbot-task` (no `:N` suffix → always uses latest revision) |
| `ECS_CONTAINER_NAME` | `matchbot` |
| `ECS_SUBNET_ID` | your subnet ID |
| `ECS_SECURITY_GROUP` | `matchbot-ecs-sg` |
| `S3_BUCKET` | `rilds` |

**IAM permissions needed on this Lambda's execution role:**
- `ecs:RunTask` scoped to the task definition ARN
- `iam:PassRole` (condition: `iam:PassedToService = ecs-tasks.amazonaws.com`)
  — required because `run_task` must pass the task's execution/task role to
  ECS

### 4.4 Glue trigger Lambda

**Lambda console → Create function** → `matchbot-glue-trigger`

Deploy the code from `scripts/lambda_function_glue.py`. Same S3-key parsing
and provider-mapping logic as the ECS Lambda, but calls
`glue.start_job_run(...)` instead.

**Environment variables:**

| Key | Example |
|---|---|
| `GLUE_JOB_NAME` | `matchbot-run` |
| `DATABASE_URL` | `postgresql://user:pass@host:5432/matchbot` |
| `DB_SCHEMA` | your schema |
| `CONFIG_S3_URI` | `s3://rilds/glue/config/` |
| `WHEEL_S3_URI` | `s3://rilds/glue/wheels/matchbot-0.1.0-py3-none-any.whl` |

**IAM permission needed:** `glue:StartJobRun` scoped to the job's ARN.

### 4.5 Grant EventBridge permission to invoke each Lambda

This step is easy to miss and produces a *silent* failure: EventBridge
reports a `FailedInvocation` in its own CloudWatch metrics, but **nothing
appears in the Lambda's own logs** (the log group won't even exist yet,
since Lambda only creates it on first successful invocation).

**Lambda console → (each function) → Configuration → Permissions →
Resource-based policy statements → Add permissions:**
- Service: EventBridge (`events.amazonaws.com`)
- Action: `lambda:InvokeFunction`
- Source ARN: the specific EventBridge rule's ARN (scopes the grant to just
  that rule)

**How to verify this is the problem, if a trigger silently doesn't fire:**
1. CloudWatch console → Metrics → All metrics → Events (EventBridge
   namespace) → By Rule Name → check `Invocations` and `FailedInvocations`
   for your rule
2. If both show activity (e.g. 1 invocation, 1 failure) but the Lambda's own
   log group doesn't exist, this permission is the cause
3. Verify directly: `aws lambda get-policy --function-name <name>` — check
   the `Principal` is `events.amazonaws.com` and the `Condition.ArnLike`
   matches your actual rule's ARN exactly (get the real ARN via
   `aws events list-rules`)
4. **Also check the rule's target itself** — `aws events list-targets-by-rule
   --rule <rule-name>` shows the exact IAM `RoleArn` EventBridge uses to
   invoke the target. If a `RoleArn` is present, EventBridge assumes *that*
   role to call the Lambda (a completely separate permission path from the
   Lambda's own resource-based policy) — the role must have
   `lambda:InvokeFunction` on the correct function ARN. A role scoped to only
   one of your two Lambdas will silently fail to invoke the other.

---

## Part 5 — Known issues encountered and fixed

This section documents real failures hit while standing this up, in case
they recur (e.g. after a dependency upgrade or a new provider onboarding).

| Symptom | Root cause | Fix |
|---|---|---|
| `InvalidInputException: At least one security group must open all ingress ports` | Glue connection's security group has no self-referencing all-traffic rule | Add inbound rule: all traffic, source = the security group itself (section 2.3) |
| `pip install failed: ... is not a valid wheel filename` | Wheel downloaded to a renamed local path (e.g. `matchbot-latest.whl`) — pip validates filenames per PEP 427 | Keep the original filename from the S3 key when downloading (already fixed in `scripts/glue_job.py`) |
| `ModuleNotFoundError: No module named 'yaml'` | Wheel installed with `pip install --no-deps`, so dependencies never landed | Install with `pip install <wheel>[aws]` (no `--no-deps`); already fixed |
| `Providers directory not found` | `config/providers/` never uploaded to S3, or upload incomplete | Verify `s3://.../config/` mirrors the local `config/` tree exactly |
| `FileExistsError: ... 'providers'` | An S3 "folder placeholder" object (zero-byte key ending in `/`) collided with the real directory during config sync | Fixed in `scripts/glue_job.py` — skips any S3 key ending in `/` |
| `OperationalError: failed to resolve host ...` | Typo/duplication in the `--database_url` parameter's hostname | Copy the exact endpoint from RDS console → Connectivity & security |
| `ecs:RunTask ... not authorized` | Lambda's execution role missing `ecs:RunTask` / `iam:PassRole` | Add both permissions to the Lambda's role (section 4.3) |
| Lambda never invoked; no log group; EventBridge shows 1 Invocation + 1 FailedInvocation | Lambda's resource-based policy missing the `events.amazonaws.com` invoke grant, **or** the rule's target uses an IAM `RoleArn` scoped to a different Lambda | Add the resource-based policy statement (section 4.5); check `list-targets-by-rule` for a `RoleArn` and verify its policy covers the right function ARN |
| `ComputeError: could not append value: N of type: i64 to the builder` | `pl.DataFrame(rows)` on a large row list defaults to sampling only the first 100 rows for type inference; a column that's `None` in all sampled rows gets the wrong dtype and fails once a real value appears later | Fixed: pass `infer_schema_length=None` wherever building a `pl.DataFrame` from row dicts |
| ECS task exits with code 137, reason `OutOfMemoryError: container killed due to memory usage`, even at 8 GB | The pipeline materialized an entire multi-hundred-thousand-to-million-row file as Python dicts (in LAND write, STAGE write, and MATCH) all at once, several times over, before writing anything to the database | Fixed: LAND, STAGE, and MATCH now process in fixed-size batches (50,000 rows), writing each batch to the database immediately before processing the next |
| A code/config fix appears not to work after redeploying | Stale artifact — either the wheel in S3 was rebuilt from stale local files (check `dist/*.whl` timestamp against when the fix was actually made), or a new image was pushed to ECR without creating a new ECS task definition revision | Always verify: `unzip -p dist/*.whl <path/to/file.py> \| grep <expected fix>` before uploading; always click **Create new revision** on the ECS task definition after every image push |

---

## Part 6 — Cost notes

Rough Fargate (us-east-2) pricing used for comparison during setup:
- vCPU: $0.04048/vCPU-hour
- Memory: $0.004445/GB-hour

Rough Glue Spark pricing: $0.44/DPU-hour, 2 DPU minimum, billed per second
(1-minute minimum).

**Practical takeaway:** ECS cost scales with actual run duration (cheap for
short runs, but a very long-running task can eventually cost more than
Glue's fixed floor). Glue's cost is dominated by the 2 DPU Spark minimum
regardless of how fast the job actually completes — making a Glue job faster
does not meaningfully reduce its cost, but making an ECS task faster does.
