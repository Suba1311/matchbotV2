# MatchBot V2

A headless, fully-orchestrated **multi-provider member-matching ETL pipeline**.
It ingests provider files (Excel / CSV / fixed-width), cleanses and standardizes
them, maps them to a canonical schema, and matches them against an authoritative
**Member Universe** — emitting matched records to a TARGET table and
unmatched/ambiguous records to an ERROR table for optional async review.

Built to be **config-driven** (no hardcoded columns), **modular / loosely
coupled** (swap a matcher, reader, or storage backend without touching the
rest), and **portable** across **AWS Fargate**, **AWS Glue**, and **Snowflake**
behind a thin runtime-adapter boundary.

```
 files ─▶ 1·Parse ─▶ 2·Cleanse/DQ ─▶ 3·Map to Canonical ─▶ 4·Match vs Member Universe
                                                                  ├─▶ TARGET (matched + member_id)
                                                                  └─▶ ERROR  (unmatched / ambiguous)
   ▲ config/ (global.yaml + providers/*.yaml)        AUDIT/Run Log ◀─ row counts · timings · match rate · DQ
```

## Why V2

The legacy Django system baked matching attributes into Python model classes and
required manual steps at every hop. V2 fixes both:

| Concern | Legacy | V2 |
|---|---|---|
| Matching columns | Hardcoded in model classes | **Config (YAML)** |
| Add a provider | New model class + DB rows + deploy | **One YAML file** |
| Run the pipeline | Manual trigger per step | **One orchestrated run, no manual steps** |
| Runtime | Django/server only | **Fargate / Glue / Snowflake** behind one interface |
| Metrics | Ad-hoc | **Audit table + structured JSON logs** |

## Quickstart

```bash
# 1. Install (uv manages the venv + exact locked deps)
uv sync --extra dev

# 2. Configure — point at your existing local Postgres or RDS
cp .env.example .env
#   edit DATABASE_URL and DB_SCHEMA

# 3. Create tables in the configured schema (idempotent)
uv run matchbot init-db

# 4. Generate synthetic sample data (no PII) and seed the Member Universe
uv run python scripts/gen_synthetic_data.py
uv run matchbot seed-members --csv data/samples/member_universe.csv

# 5. Validate config and run the pipeline locally
uv run matchbot validate-config
uv run matchbot run --provider provider2_unemployment --input data/samples/
```

Commands: `run`, `init-db`, `seed-members`, `validate-config`, `list-providers`.
Set `MATCHBOT_LOG_JSON=true` for JSON logs (prod / CloudWatch); the run summary
line carries counts, per-stage timings, match rate, and DQ metrics — the same
data persisted to the `audit_run` table.
```

## Onboarding

**A new developer:** `uv sync` → set `DATABASE_URL` + `DB_SCHEMA` in `.env` →
`matchbot init-db`. No Docker, no manual schema, no code.

**A new provider:** drop one validated YAML file in `config/providers/`. No code,
no deploy. Standardization maps (gender, name suffixes), match thresholds, and DQ
rules all live in `config/global.yaml`.

## Configuration

* `config/global.yaml` — canonical attribute dictionary, standardization maps,
  blocking keys, the matcher chain (weights + thresholds), and DQ rules. Shared
  by all providers.
* `config/providers/*.yaml` — one per provider: file format, column mappings,
  transforms. The entire onboarding surface.

Both are validated by Pydantic on load with cross-reference checks, so a bad
config fails fast with a precise message before any data is touched.

## Database

Uses your existing Postgres (local or RDS) via `DATABASE_URL`. **Single schema
for now**, selected purely by the `DB_SCHEMA` env var — no schema name is
hardcoded anywhere. Add another schema/environment later by changing the env var
and re-running `init-db`.

## Layout

```
config/                  global.yaml + providers/*.yaml
src/matchbot/
  domain/                canonical schema, enums (pure, no deps)
  config/                Pydantic models, loader, env settings
  pipeline/              parse · cleanse · canonical · match stages + orchestrator
  matching/              deterministic / fuzzy matchers, blocking, standardizers
  storage/               repository interface + Postgres impl
  runtime/               local / fargate / glue / snowflake adapters
  audit/                 run metrics + audit persistence
  notify/                completion notifiers (log / SES)
scripts/                 synthetic data generator
tests/                   unit + integration
```

## Development

```bash
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy src          # type-check
uv run pytest            # tests
```

Targets Python `>=3.11` (develop on 3.13). The 3.11 floor keeps AWS Glue 5.0 and
Snowflake Snowpark viable for the same codebase.
