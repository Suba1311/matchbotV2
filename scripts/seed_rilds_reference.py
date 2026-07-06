"""Load data/samples/rilds_reference_100k.csv into the rilds_reference table.

Uses the same Settings/DATABASE_URL/DB_SCHEMA env vars as `matchbot init-db`,
so it works against local Postgres or RDS with no separate connection setup —
point the usual env vars at the target DB before running:

    DATABASE_URL=postgresql://... DB_SCHEMA=rilds \\
        uv run python scripts/seed_rilds_reference.py

Assumes the table already exists (create it first via `matchbot init-db` —
safe/idempotent, skips rilds_reference if it's already there with data).
Blank CSV values are loaded as NULL, not empty strings, since most
rilds_reference columns are optional identifiers.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from sqlalchemy import create_engine, insert

from matchbot.config.settings import get_settings
from matchbot.storage.schema import build_metadata, search_path_sql

BATCH_SIZE = 5_000


def _normalize_url(url: str) -> str:
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def _load_rows(csv_path: Path) -> list[dict]:
    rows = []
    with csv_path.open("r", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            # Blank string -> NULL for every column; CSV has no quoting to
            # distinguish "" from missing, and every field here is optional.
            rows.append({k: (v if v != "" else None) for k, v in r.items()})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv",
        type=Path,
        default=Path("data/samples/rilds_reference_100k.csv"),
        help="Path to the rilds_reference sample CSV (default: data/samples/rilds_reference_100k.csv).",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Clear rilds_reference before loading (default: plain insert, no clearing).",
    )
    args = parser.parse_args()
    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}. Run scripts/gen_rilds_reference.py first.")

    settings = get_settings()
    md = build_metadata(settings.db_schema)
    table = next(t for t in md.tables.values() if t.name == "rilds_reference")
    engine = create_engine(_normalize_url(settings.database_url), pool_pre_ping=True, future=True)

    print(f"Reading {args.csv} ...")
    rows = _load_rows(args.csv)
    print(f"  {len(rows):,} rows to load")

    with engine.begin() as conn:
        conn.execute(search_path_sql(settings.db_schema))
        if args.replace:
            conn.execute(table.delete())
            print("  cleared existing rilds_reference rows")

    loaded = 0
    with engine.begin() as conn:
        conn.execute(search_path_sql(settings.db_schema))
        for i in range(0, len(rows), BATCH_SIZE):
            batch = rows[i : i + BATCH_SIZE]
            conn.execute(insert(table), batch)
            loaded += len(batch)
            print(f"  loaded {loaded:,}/{len(rows):,}")

    print(f"Done. Loaded {loaded:,} rows into rilds_reference (schema={settings.db_schema}).")


if __name__ == "__main__":
    main()
