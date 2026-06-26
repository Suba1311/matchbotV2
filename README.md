# MatchBot V2

A headless, fully-orchestrated **multi-provider member-matching ETL pipeline**.
It ingests provider files (CSV / Excel / fixed-width), dumps the raw file to a
**LAND** archive, cleanses and standardizes the data, maps it to a canonical
schema in a **STAGE** table, and matches it against an authoritative **Member
Universe** — writing matched records to **TARGET** and unmatched/ambiguous
records to **ERROR** for optional async review. Every run writes a row to a
**run-log** with counts, timings, match rate, and DQ metrics.

Built to be **config-driven** (no hardcoded columns), **modular / loosely
coupled** (swap a matcher, reader, or storage backend without touching the
rest), and **portable** across **AWS Fargate**, **AWS Glue**, and **Snowflake**
behind a thin runtime-adapter boundary.

```
 file ─▶ 1·Parse ─▶ LAND ─▶ 2·Cleanse/DQ ─▶ 3·Map to Canonical ─▶ STAGE ─▶ 4·Match
                 (raw dump)                                                    │
                                                              ┌────────────────┴───────────────┐
                                                              ▼                                 ▼
                                                    TARGET (matched + member_id)     ERROR (unmatched / ambiguous)
   ▲ config/ (global.yaml + providers/*.yaml)       pipeline_runs ◀─ counts · timings · match rate · DQ
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
| Metrics | Ad-hoc | **`pipeline_runs` run-log + structured JSON logs** |

## Prerequisites

- **[uv](https://docs.astral.sh/uv/)** (Python package/venv manager)
- **PostgreSQL** running locally or reachable (e.g. AWS RDS). No Docker required.
- Python is managed by uv; the project targets **>=3.11** (develop on 3.13).

## Quickstart

```bash
# 1. Install dependencies into a local venv (uv reads the lockfile)
uv sync --extra dev

# 2. Configure — point at your Postgres and pick a schema
cp .env.example .env
#    edit DATABASE_URL and DB_SCHEMA (see Configuration below)

# 3. Create the schema + core tables (idempotent)
uv run matchbot init-db

# 4. (optional) Validate config and list providers
uv run matchbot validate-config
uv run matchbot list-providers

# 5. Seed the Member Universe (reference data to match against)
uv run matchbot seed-members --csv data/samples/ride_member_universe.csv

# 6. Run the pipeline for a provider's files
uv run matchbot run --provider ride_enrollment --input data/samples/ride_enrollment_1k.csv
```

**Expected output (1k file):**
`590/1000 matched (59.0%), 288 unmatched, 122 ambiguous, ~0.9s [success]`

> **Order matters once:** `init-db` → `seed-members` → `run`. After that you only
> repeat `run` for new files. Re-seed only when the Member Universe changes.

## CLI commands

| Command | What it does |
|---|---|
| `matchbot init-db` | Create the schema + core tables and blocking indexes (idempotent). |
| `matchbot seed-members --csv <file>` | Load the Member Universe (truncates + reloads). |
| `matchbot run --provider <id> --input <file-or-dir>` | Run the full pipeline end to end. |
| `matchbot validate-config` | Load + cross-validate all YAML config; non-zero exit on error. |
| `matchbot list-providers` | List configured providers. |

`--input` accepts a single file or a directory (it then processes every file
matching the provider's `file_glob`).

## What a run does (no manual steps)

For each input file the orchestrator runs the whole pipeline and persists at
every hop:

1. **Parse** the file (CSV / Excel / fixed-width) into a frame.
2. **LAND** — dump the raw row verbatim into `<provider_code>_land` (e.g.
   `ride_land`): an exact, all-text mirror of the file (placeholders like
   `NULL` / `00:00.0` preserved). Immutable raw archive.
3. **Map to Canonical** — rename source columns to canonical attributes.
4. **Cleanse & DQ** — standardize values (gender map, names), derive blocking
   fields (metaphone, `last_name8`, `birth_year/month/day`), record DQ metrics.
5. **STAGE** — insert canonical + blocking rows (`match_status=PENDING`).
6. **Match** vs the Member Universe (blocking → matcher chain), then update each
   stage row's status in place and write **TARGET** / **ERROR**.
7. **pipeline_runs** — write the run-log row (counts, timings, match rate, DQ).

## Database tables

All in the schema named by `DB_SCHEMA`:

| Table | Holds |
|---|---|
| `<provider>_land` (e.g. `ride_land`) | Raw file dump, verbatim, one table per provider |
| `stage` | Canonical + derived blocking columns + match status (updated in place) |
| `member_universe` | Authoritative member master (read-only reference) |
| `target` | Matched rows: `member_id`, `match_method`, `match_score` **+ the matching attributes used** |
| `error` | Unmatched / low-confidence rows: `decision`, `reason` **+ the matching attributes** |
| `pipeline_runs` | One row per run: counts at each hop, timings, match rate, DQ, status |

`target` and `error` carry the **matching-attribute columns** (`first_name`,
`last_name`, `sasid`, `last_name_metaphone`, `last_name8`, …) so each row is
self-explanatory — you can see *how* it matched or *why* it failed without a
join back to `stage`.

## Inspect results

```bash
# Use your psql; on this machine the Homebrew binary lives here:
export PATH="/opt/homebrew/opt/postgresql@15/bin:$PATH"   # adjust to your install
SCHEMA=$(grep DB_SCHEMA .env | cut -d= -f2)

# Run-log (the benchmarking surface): counts, match rate, duration
psql -d matchbot -c "SELECT id, source_uri, rows_landed, rows_staged, rows_matched, rows_unmatched, round(match_rate::numeric,3) match_rate, duration_seconds, status FROM ${SCHEMA}.pipeline_runs ORDER BY id;"

# Matched rows — see the attributes that matched
psql -d matchbot -c "SELECT member_id, match_method, match_score, first_name, last_name, sasid FROM ${SCHEMA}.target LIMIT 5;"

# Failed rows — see what the record had when it failed
psql -d matchbot -c "SELECT decision, first_name, last_name, sasid, left(reason,40) FROM ${SCHEMA}.error LIMIT 5;"

# LAND — verbatim file dump
psql -d matchbot -c "SELECT recid, firstname, lastname, sasid, enrollbegin FROM ${SCHEMA}.ride_land LIMIT 5;"
```

## Onboarding

**A new developer:** `uv sync --extra dev` → set `DATABASE_URL` + `DB_SCHEMA` in
`.env` → `matchbot init-db`. No Docker, no manual schema, no code. ~5 min.

**A new provider:** drop one validated YAML file in `config/providers/`. No code,
no deploy. Standardization maps (gender, name suffixes), match thresholds, and DQ
rules all live in `config/global.yaml`.

## Configuration

* `config/global.yaml` — canonical attribute dictionary, standardization maps,
  blocking keys, the matcher chain (weights + thresholds), and DQ rules. Shared
  by all providers.
* `config/providers/*.yaml` — one per provider: `provider_code`, `dataset_name`,
  file format, `column_mappings` (file column → canonical attribute), transforms,
  optional per-provider matcher override. The entire onboarding surface.

Both are validated by Pydantic on load with cross-reference checks, so a bad
config fails fast with a precise message before any data is touched.

Bundled providers: `ride_enrollment` (real RIDE schema), plus synthetic
`provider1_education` (xlsx), `provider2_unemployment` (csv),
`provider3_health` (fixed-width).

## Environment variables (`.env`)

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Postgres connection, e.g. `postgresql://user:pass@host:5432/matchbot` |
| `DB_SCHEMA` | Schema for all MatchBot tables. Never hardcoded — switch schemas via this var. |
| `MEMBER_UNIVERSE_URL` | *(optional)* separate connection for the read-only Member Universe |
| `MATCHBOT_RUNTIME` | `local` (default) \| `fargate` \| `glue` \| `snowflake` |
| `MATCHBOT_CONFIG_DIR` | Config directory (default `config`) |
| `MATCHBOT_LOG_LEVEL` | `INFO` (default), `DEBUG`, … |
| `MATCHBOT_LOG_JSON` | `true` → JSON logs (prod / CloudWatch); `false` → human console |

The DB schema comes purely from `DB_SCHEMA`; switch schemas/environments by
changing the env var and re-running `init-db`. No schema name is hardcoded.

## Sample data

```bash
# Regenerate the RIDE enrollment files (1k / 10k / 100k) + member universe
uv run python scripts/gen_ride_enrollment.py
```

## Common workflows

**JSON logs** (full run metrics on one line, for CloudWatch / aggregators):

```bash
MATCHBOT_LOG_JSON=true uv run matchbot run --provider ride_enrollment --input data/samples/ride_enrollment_1k.csv
```

**Fresh start** (wipe and rebuild — required after a schema change):

```bash
export PATH="/opt/homebrew/opt/postgresql@15/bin:$PATH"   # adjust to your install
SCHEMA=$(grep DB_SCHEMA .env | cut -d= -f2)
psql -d matchbot -c "DROP SCHEMA IF EXISTS ${SCHEMA} CASCADE;"
uv run matchbot init-db
uv run matchbot seed-members --csv data/samples/ride_member_universe.csv
```

> **When to recreate the schema:** any time the table columns change (a new
> canonical attribute, new match-attribute columns, etc.). `init-db` only creates
> *missing* tables — it does not alter existing ones.

**Benchmark the three sizes:**

```bash
for n in 1k 10k 100k; do
  uv run matchbot run --provider ride_enrollment --input data/samples/ride_enrollment_${n}.csv
done
```

## Layout

```
config/                  global.yaml + providers/*.yaml
src/matchbot/
  domain/                canonical schema, enums, match-attribute set (pure, no deps)
  config/                Pydantic models, loader, env settings
  pipeline/              parse · canonical · cleanse · match stages + orchestrator
  matching/              deterministic / fuzzy matchers, blocking, standardize, derive, vocab
  storage/               repository interface + Postgres impl + table schema
  runtime/               local / fargate / glue / snowflake adapters
  audit/                 run metrics
  notify/                completion notifiers (log / SES)
scripts/                 synthetic data generators
tests/                   unit + integration (run without a database)
```

## Development

```bash
uv run ruff check .      # lint
uv run ruff format .     # format
uv run mypy src          # type-check
uv run pytest            # tests (use an in-memory repo — no DB needed)
```

Targets Python `>=3.11` (develop on 3.13). The 3.11 floor keeps AWS Glue 5.0 and
Snowflake Snowpark viable for the same codebase.

## Portability (Fargate / Glue / Snowflake)

The pure pipeline core never imports a cloud SDK. Platform specifics live behind
two small interfaces in `runtime/`: a `FileSystem` (local disk / S3 / stage) and
a `Repository` (Postgres today). Select the adapter with `MATCHBOT_RUNTIME`.
`local` and `fargate` (S3 + RDS) are implemented; `glue` and `snowflake` are
stubs that document exactly what to fill in. Install platform extras as needed:
`uv sync --extra aws` (boto3), `--extra snowflake`, `--extra fast`
(connectorx/adbc).
