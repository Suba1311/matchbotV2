# MatchBot — AWS Production Setup Guide

This document describes how to deploy and operate MatchBot on AWS. MatchBot
can run on either **AWS Glue** or **AWS ECS Fargate** — both execute the same
pipeline code against the same database schema, so they are interchangeable
compute options rather than two separate systems. This guide covers both.

**Contents**
1. [Architecture overview](#1-architecture-overview)
2. [Prerequisites](#2-prerequisites)
3. [Database setup (RDS)](#3-database-setup-rds)
4. [AWS Glue setup](#4-aws-glue-setup)
5. [AWS ECS Fargate setup](#5-aws-ecs-fargate-setup)
6. [Automated triggering on file upload](#6-automated-triggering-on-file-upload)
7. [Other AWS services used](#7-other-aws-services-used)
8. [Matching logic](#8-matching-logic)
9. [Cost estimation](#9-cost-estimation)
10. [Operational notes](#10-operational-notes)

---

## 1. Architecture overview

```
S3 (file uploaded to data/input/<provider>/)
        │
        ▼
  EventBridge rule (Object Created)
        │
        ▼
  Trigger Lambda (per compute platform)
        │
        ├──────────────────────────────┬──────────────────────────────┐
        ▼                              ▼                              
   AWS Glue job                   ECS Fargate task                    
   (fetches code + config          (code + config baked
    from S3 at runtime)             into the container image)         
        │                              │
        └──────────────┬───────────────┘
                        ▼
                  RDS (PostgreSQL)
        rilds_stage · rilds_matched · rilds_error
        rilds_land_rejects · rilds_audit · rilds_reference
        <provider>_land
                        │
                        ▼
              SES (run-summary email)
```

Both platforms:
- Read one provider file per run
- Parse → cleanse/standardize → map to canonical schema → match against
  `rilds_reference` → write results
- Write to the same RDS schema
- Emit the same run-summary notification

Choosing between them is an infrastructure decision (see [Section 9](#9-cost-estimation)), not a functional one.

---

## 2. Prerequisites

Before starting either platform's setup:

- An S3 bucket for input files (and, for Glue, packaged code) — referred to
  as `rilds` throughout this guide
- An RDS PostgreSQL instance, reachable from the compute you intend to use
- A VPC with subnets that both RDS and the compute (Glue/ECS) can use
- (Optional) A verified SES sender identity, if email run-summaries are
  wanted

### Build the deployable package

MatchBot ships as a Python wheel. Build it from the project root:

```bash
uv build --wheel
```

This produces `dist/matchbot-0.1.0-py3-none-any.whl`. Rebuild this whenever
the application code changes — Glue installs this wheel at runtime, and ECS
images are built from the same source tree.

---

## 3. Database setup (RDS)

### 3.1 Instance configuration

- Engine: PostgreSQL 14+
- Place RDS in a private subnet; only the security groups used by Glue and
  ECS (Sections 4 and 5) should have inbound access on port 5432
- Note the instance endpoint, database name, and credentials — these form
  the `DATABASE_URL` used by every job: `postgresql://<user>:<pass>@<endpoint>:5432/<dbname>`
- `DB_SCHEMA` selects the PostgreSQL schema MatchBot uses. No schema name is
  hardcoded anywhere in the application, so multiple environments (e.g.
  dev/stage/prod) can share one RDS instance under different schemas.

### 3.2 Initial schema creation

Run once per environment/schema:

```bash
uv run matchbot init-db
```

This is idempotent — safe to re-run, and safe to leave as an automatic step
at the start of every pipeline run (which is how the Glue job and ECS task
are configured). It creates all pipeline-owned tables and indexes if they do
not already exist, and does nothing if they do.

If the machine running this command cannot reach RDS directly (e.g. private
subnet, no VPN), run it instead from **AWS CloudShell** or any host with
network access into the VPC.

### 3.3 Tables

Created automatically by `init-db` / `init_schema()`:

| Table | Purpose |
|---|---|
| `rilds_audit` | One row per pipeline run — status, row counts at each stage, timings, match rate, DQ metrics. |
| `<provider>_land` | Per-provider immutable raw archive of every source file, one table per provider, created on first run. |
| `rilds_land_rejects` | Source rows that failed structural parsing (e.g. field-count mismatch), retained verbatim for review. |
| `rilds_stage` | Canonical working table: incoming rows mapped to standard attributes plus derived blocking columns, updated in place with match results. |
| `rilds_matched` | One row per successful match — which reference record it matched, the score, and which rule produced it. |
| `rilds_error` | One row per unmatched or ambiguous record, with a reason, for manual review. |
| `member_universe` | Legacy reference table, retained for compatibility. Not used by the active matching path. |

**Not created or managed by the pipeline:**

| Table | Purpose |
|---|---|
| `rilds_reference` | The authoritative matching universe — a person/identifier/address reference table populated from an external source system. The pipeline will create this table if it does not exist (so `init-db` never fails on a fresh account), but it never writes to it and never truncates or reseeds it. Loading and refreshing this table's data is a separate, deliberate operational process outside the pipeline's normal run cycle. |

---

## 4. AWS Glue setup

### 4.1 Upload artifacts to S3

Under the project's S3 bucket:

1. `glue/wheels/` — upload `dist/matchbot-0.1.0-py3-none-any.whl`
2. `glue/scripts/` — upload `scripts/glue_job.py` (used only as the initial
   source when the job is first created — see Section 10.1)
3. `glue/config/` — upload the entire `config/` directory, preserving its
   structure (`global.yaml` plus everything under `providers/`)

### 4.2 IAM role for Glue

Create a role with:
- Trusted entity: AWS Glue
- Managed policy: `AWSGlueServiceRole`
- Inline policy: `s3:GetObject` / `s3:ListBucket` on the project bucket
- If using SES notifications: `ses:SendEmail`, `ses:SendRawEmail`

### 4.3 Security group for Glue's VPC connection

Glue jobs that connect to RDS require a VPC-attached connection, which in
turn requires a security group with a self-referencing all-traffic rule
(this is a Glue platform requirement for internal Spark node communication,
unrelated to RDS access):

1. Create a security group in the same VPC as RDS (e.g. `matchbot-glue-sg`)
2. Edit its inbound rules: add a rule allowing all traffic with itself as
   the source

### 4.4 Allow Glue to reach RDS

On the RDS instance's security group, add an inbound rule: PostgreSQL
(5432), source = the Glue security group created above.

### 4.5 Create a Glue JDBC connection

- Type: JDBC
- JDBC URL: `jdbc:postgresql://<rds-endpoint>:5432/<dbname>`
- VPC / subnet: same as RDS
- Security group: the one created in 4.3

### 4.6 Create the Glue job

Using the Script Editor (not the Visual ETL canvas):

1. Upload `scripts/glue_job.py` as the initial script
2. **Job details:**
   - Name: `matchbot-run`
   - IAM role: the role created in 4.2
   - Type: Spark
   - Glue version: latest 5.x (Python 3.11 runtime)
   - Worker type: G.1X, 2 workers
3. **Connections:** attach the JDBC connection from 4.5
4. **Job parameters:**

   | Key | Value |
   |---|---|
   | `--wheel_s3_uri` | `s3://rilds/glue/wheels/matchbot-0.1.0-py3-none-any.whl` |
   | `--config_s3_uri` | `s3://rilds/glue/config/` |
   | `--database_url` | `postgresql://<user>:<pass>@<rds-endpoint>:5432/<dbname>` |
   | `--db_schema` | target schema name |
   | `--command` | `run` |
   | `--notifier` | `ses` (omit, or set `log`, to disable email) |
   | `--ses_sender` | verified SES sender address |
   | `--ses_recipients` | comma-separated recipient list |

5. Save the job

### 4.7 Running the job

Run with parameters:
- `--provider` = the provider id (e.g. `ride_enrollment`)
- `--input_uri` = the S3 URI of the file to process

Job output and errors are available under the job's **Runs** tab.

---

## 5. AWS ECS Fargate setup

### 5.1 Build and push the container image

```bash
docker build -t matchbot:latest .
aws ecr get-login-password --region <region> | docker login --username AWS --password-stdin <account-id>.dkr.ecr.<region>.amazonaws.com
docker tag matchbot:latest <account-id>.dkr.ecr.<region>.amazonaws.com/matchbot:latest
docker push <account-id>.dkr.ecr.<region>.amazonaws.com/matchbot:latest
```

### 5.2 IAM role for the ECS task

Create a task role with:
- S3 read access, if the task reads input files directly
- If using SES notifications: `ses:SendEmail`, `ses:SendRawEmail`

### 5.3 Create the ECS cluster

- Infrastructure: AWS Fargate
- Name: e.g. `matchbot-cluster`

### 5.4 Create the ECS task definition

- Launch type: Fargate
- Task CPU: 1 vCPU
- Task memory: 2–4 GB is sufficient at current data volumes (the pipeline
  processes files in bounded batches, so memory use does not scale with file
  size)
- Container:
  - Name: `matchbot`
  - Image: the ECR image pushed in 5.1
  - Environment variables: `DATABASE_URL`, `DB_SCHEMA`, and (optionally)
    `MATCHBOT_NOTIFIER=ses`, `MATCHBOT_SES_SENDER`, `MATCHBOT_SES_RECIPIENTS`,
    `AWS_REGION`
  - Logging: `awslogs` driver, to a CloudWatch log group

Each time a new image is pushed under the same tag, create a **new task
definition revision** — ECS does not pick up a new image under an existing
revision automatically.

### 5.5 Security groups for ECS → RDS

1. Create (or reuse) a security group for the ECS task (e.g.
   `matchbot-ecs-sg`)
2. On the RDS security group, add an inbound rule: PostgreSQL (5432),
   source = `matchbot-ecs-sg`

### 5.6 Running a task manually

From the ECS console, run a new task with a container command override:
```
["run", "--provider", "<provider_id>", "--input", "<s3-uri-of-file>"]
```

---

## 6. Automated triggering on file upload

Both platforms are triggered the same way: an S3 upload fires an
EventBridge rule, which invokes a Lambda function that launches the
appropriate compute.

### 6.1 Enable EventBridge notifications on the bucket

S3 console → bucket → Properties → Amazon EventBridge → **On**.

### 6.2 Create the EventBridge rule

Event pattern:
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

Add each trigger Lambda (Section 6.3, 6.4) as a target.

### 6.3 ECS trigger Lambda

Deploy `scripts/lambda_function.py`. It maps the uploaded file's S3 prefix
to a provider id and calls `ecs.run_task(...)`.

Environment variables:

| Key | Example |
|---|---|
| `ECS_CLUSTER` | `matchbot-cluster` |
| `ECS_TASK_DEFINITION` | `matchbot-task` |
| `ECS_CONTAINER_NAME` | `matchbot` |
| `ECS_SUBNET_ID` | target subnet id |
| `ECS_SECURITY_GROUP` | `matchbot-ecs-sg` |
| `S3_BUCKET` | `rilds` |

Execution role requires `ecs:RunTask` (scoped to the task definition) and
`iam:PassRole` (condition: `iam:PassedToService = ecs-tasks.amazonaws.com`).

### 6.4 Glue trigger Lambda

Deploy `scripts/lambda_function_glue.py`. Same provider-mapping logic, calls
`glue.start_job_run(...)`.

Environment variables:

| Key | Example |
|---|---|
| `GLUE_JOB_NAME` | `matchbot-run` |
| `DATABASE_URL` | connection string |
| `DB_SCHEMA` | target schema |
| `CONFIG_S3_URI` | `s3://rilds/glue/config/` |
| `WHEEL_S3_URI` | `s3://rilds/glue/wheels/matchbot-0.1.0-py3-none-any.whl` |

Execution role requires `glue:StartJobRun` scoped to the job's ARN.

### 6.5 Grant EventBridge permission to invoke each Lambda

On each Lambda's resource-based policy, add a statement allowing
`events.amazonaws.com` to call `lambda:InvokeFunction`, scoped to the
EventBridge rule's ARN.

---

## 7. Other AWS services used

| Service | Role |
|---|---|
| **S3** | Stores input files, and (Glue only) the packaged wheel and config. Source of the upload events that drive automated runs. |
| **EventBridge** | Detects file uploads and routes them to the trigger Lambdas. |
| **Lambda** | Two small functions that translate an S3 upload into a Glue job run or an ECS task run. |
| **AWS Glue** | Spark-based compute option; installs the application at runtime from S3. |
| **ECS Fargate** | Container-based compute option; runs a prebuilt image. |
| **ECR** | Hosts the Docker image used by ECS. |
| **CodeBuild** | Optional — builds and pushes the Docker image from source. |
| **RDS (PostgreSQL)** | System of record for all pipeline tables. |
| **IAM** | Roles for Glue, the ECS task, and each Lambda; resource-based Lambda policy for EventBridge invocation. |
| **CloudWatch** | Logs for Glue runs, ECS tasks, and Lambda invocations. |
| **SES** | Optional — sends the run-summary email at the end of each pipeline run. |
| **VPC / Security groups** | Network boundary between compute and RDS. |

---

## 8. Matching logic

Matching is fully configuration-driven (`config/global.yaml`,
`matching.matchers`) and applies identically to every provider — there is no
per-provider matching code. All rules currently configured are **exact-match
only** (no fuzzy matching, no confidence scoring):

| Order | Rule | Attributes compared |
|---|---|---|
| 1 | External id | The provider's own identifier (e.g. RIDE's SASID), mapped via that provider's configuration |
| 2 | SSN | Social Security Number |
| 3 | Name + date of birth | Standardized first name, last name, and birth date |
| 4 | Name + address | Standardized first name, last name, and address line 1 |

**Evaluation order, per record:** rules are tried in the order above, for
each incoming record individually. If a rule finds no match, the next rule
is tried for that same record. A record is only evaluated against a rule if
it has values for every attribute that rule requires; a provider whose file
never supplies a given attribute (for example, no date-of-birth column) has
that rule skipped for its entire file, not just individual records.

**Match outcome:** the first rule to find a match wins (score is always
`1.0`, since all rules are exact-equality). The result records which
attributes were compared and which rule produced the match. Records that
exhaust all applicable rules without a match are written to `rilds_error`
for review.

**Blocking:** before scoring, candidate reference records are narrowed using
configured blocking keys (external id, SSN, last name + DOB, first + last +
DOB, first + last name, or last name with phonetic matching). This is a
performance optimization only — it does not affect which matches are
accepted.

---

## 9. Cost estimation

Approximate figures for comparing the two compute options; confirm current
rates for your region before budgeting.

| Resource | Rate |
|---|---|
| Fargate vCPU | ~$0.04048 / vCPU-hour |
| Fargate memory | ~$0.004445 / GB-hour |
| Glue Spark | ~$0.44 / DPU-hour, 2 DPU minimum, billed per second with a 1-minute minimum |
| SES | $0.10 per 1,000 emails |

**Per-run cost (illustrative):**

| Run duration | ECS (1 vCPU / 2 GB) | Glue (2 DPU) |
|---|---|---|
| 1 minute | ~$0.001 | ~$0.015 (1-minute minimum) |
| 5 minutes | ~$0.004 | ~$0.073 |
| 30 minutes | ~$0.025 | ~$0.44 |

ECS cost scales directly with run duration and has no fixed floor. Glue
carries a fixed 2 DPU / 1-minute cost floor regardless of job size, which
dominates cost at typical run durations. Both are inexpensive at current
data volumes (up to roughly 1M rows per file); the choice between platforms
should generally be based on operational fit rather than cost.

---

## 10. Operational notes

### 10.1 Updating the Glue job's script after creation

Once a Glue job has been created and run at least once, AWS Glue maintains
its own managed copy of the script separate from the S3 location it was
originally uploaded from. To change the script after initial setup, edit it
directly via the job's **Script** tab in the console (or update it via the
Glue API), not by re-uploading to the original S3 location.

### 10.2 Redeploying after a code change

1. Rebuild the wheel (`uv build --wheel`)
2. **Glue:** upload the new wheel to the S3 location referenced by
   `--wheel_s3_uri`; update the script via the Script tab if it changed
3. **ECS:** rebuild and push the Docker image, then create a new task
   definition revision — pushing a new image alone does not update a
   running or future task under the previous revision

### 10.3 Onboarding a new provider

Add one YAML file under `config/providers/`, defining the file format,
column mappings, and any provider-specific overrides. No code changes are
required. For Glue, re-upload the `config/` directory to S3 after adding the
file. For ECS, rebuild the image so the new config is baked in.
