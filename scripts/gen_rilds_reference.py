"""Generate a rilds_reference sample + real-data RIDE test input files.

Sources 100,000 distinct SASID + name records from a real RIDE enrollment
extract (not committed to the repo — point ``--source`` at your local copy),
writes them to ``data/samples/rilds_reference_100k.csv`` with fabricated
SSN/birth_date/gender/address at partial coverage (SSN/DOB/gender/address are
not present in the source file at all), and separately samples four RIDE
input test files (1k/10k/100k/1m rows) from the SAME source file — at natural
random overlap with the reference set, so each shows a different, realistic
match rate rather than an engineered one.

Run:
    uv run python scripts/gen_rilds_reference.py \\
        --source ~/Downloads/RIDE_Enrollment_2024_2025_01292026.csv

Only touches data/samples/ — never modifies the source file.
"""

from __future__ import annotations

import argparse
import csv
import random
from pathlib import Path

from faker import Faker

OUT = Path("data/samples")
SEED = 2026
REFERENCE_SIZE = 100_000
INPUT_SIZES = {"real_1k": 1_000, "real_10k": 10_000, "real_100k": 100_000, "real_1m": 1_000_000}

# Independent per-field coverage for fabricated columns not present in RIDE's
# source data at all (SSN/DOB/gender/address) — mixed/lower coverage to
# simulate realistic reference-data gaps, per the agreed design.
SSN_COVERAGE = 0.50
DOB_COVERAGE = 0.55
GENDER_COVERAGE = 0.60
ADDRESS_COVERAGE = 0.45

RI_CITIES = [
    ("PROVIDENCE", "RI", "02903"),
    ("CRANSTON", "RI", "02920"),
    ("WARWICK", "RI", "02886"),
    ("PAWTUCKET", "RI", "02860"),
    ("WOONSOCKET", "RI", "02895"),
    ("COVENTRY", "RI", "02816"),
    ("BRISTOL", "RI", "02809"),
    ("NEWPORT", "RI", "02840"),
]


def _read_distinct_sasid_names(source: Path, n: int, rng: random.Random) -> list[dict]:
    """Stream the source file once, dedup by SASID, reservoir-sample ``n``.

    The file has ~1.4M rows but only ~154k distinct SASIDs (repeat rows are
    the same person's multiple enrollment records) — first-seen name per
    SASID is kept, later duplicate rows for that SASID are skipped without
    buffering the whole file in memory.
    """
    seen: dict[str, dict] = {}
    with source.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # DictReader fills missing trailing fields with None (its
            # restval, not "") on ragged rows — the source has some of
            # these, so .get(...) alone isn't enough; guard every field.
            sasid = (row.get("SASID") or "").strip()
            if not sasid or sasid in seen:
                continue
            seen[sasid] = {
                "sasid": sasid,
                "first_name": (row.get("FIRSTNAME") or "").strip(),
                "last_name": (row.get("LASTNAME") or "").strip(),
            }
    distinct = list(seen.values())
    rng.shuffle(distinct)
    return distinct[:n]


def _sample_input_rows(source: Path, n: int, rng: random.Random) -> tuple[list[str], list[list[str]]]:
    """Reservoir-sample ``n`` raw rows (any SASID, natural repeats allowed)."""
    header: list[str] = []
    reservoir: list[list[str]] = []
    with source.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        for i, row in enumerate(reader):
            if len(reservoir) < n:
                reservoir.append(row)
            else:
                j = rng.randint(0, i)
                if j < n:
                    reservoir[j] = row
    return header, reservoir


def _fake_ssn(fake: Faker) -> str:
    return f"{fake.random_int(100_000_000, 999_999_999)}"


def _fake_address(fake: Faker, rng: random.Random) -> tuple[str, str, str]:
    city, state, zip5 = rng.choice(RI_CITIES)
    return fake.street_address().upper(), city, zip5


def build_reference_rows(people: list[dict], std_config, fake: Faker, rng: random.Random) -> list[dict]:
    from matchbot.matching import standardize as S

    rows = []
    for i, person in enumerate(people, start=1):
        first_name = person["first_name"]
        last_name = person["last_name"]
        first_std = S.std_name(first_name, std_config)
        last_std = S.std_name(last_name, std_config)

        has_ssn = rng.random() < SSN_COVERAGE
        has_dob = rng.random() < DOB_COVERAGE
        has_gender = rng.random() < GENDER_COVERAGE
        has_address = rng.random() < ADDRESS_COVERAGE

        ssn = _fake_ssn(fake) if has_ssn else ""
        birth_date = (
            fake.date_of_birth(minimum_age=17, maximum_age=30).isoformat() if has_dob else ""
        )
        gender = rng.choice(["MALE", "FEMALE"]) if has_gender else ""
        if has_address:
            address1, city, zip5 = _fake_address(fake, rng)
        else:
            address1, city, zip5 = "", "", ""

        by, bm, bd = (birth_date.split("-") if birth_date else ("", "", ""))
        full_name_std = f"{first_std or ''}{last_std or ''}"

        rows.append(
            {
                "idcol_id": i,
                "person_id": i,  # 1:1 for this sample: one idcol per person, already linked
                "dataset_id": 1,  # single synthetic "RIDE extract" dataset
                "first_name": first_name,
                "middle_name": "",
                "last_name": last_name,
                "birth_date": birth_date,
                "gender": gender,
                "ssn": ssn,
                # ModelIdentifiers: only sasid is real; the other 29 are
                # genuinely unknown for this source and left blank.
                "apprentice_id": "", "brown_id": "", "bryant_id": "", "ccri_id": "",
                "college_board_id": "", "dcyf_id": "", "dlt_ern": "", "employri_id": "",
                "ged_id": "", "jwu_id": "", "kidsnet_child_id": "", "laces_id": "",
                "laces_staff_id": "", "laces_student_id": "", "lasid": "", "nspid": "",
                "ods": "", "ric_id": "", "ride_cert_id": "", "ridoh_lead_id": "",
                "risd_id": "", "rjri_id": "", "rwu_id": "", "salve_id": "",
                "sasid": person["sasid"],
                "uri_id": "", "voter_id": "", "workforce_id": "",
                "providencecollege_id": "", "netech_id": "",
                # DerivedIdentifiers, computed with the pipeline's own
                # standardize.py so they're consistent with cleanse-stage
                # output. Dual-metaphone (metaphone2) isn't computed here —
                # this sample only reproduces the primary code.
                "first_name_std": first_std or "",
                "first_name_metaphone1": S.metaphone(first_std) or "",
                "first_name_metaphone2": "",
                "first_name_transposed": "",
                "first_initial": (first_name[:1] if first_name else ""),
                "middle_name_std": "",
                "middle_initial": "",
                "last_name_std": last_std or "",
                "last_name_metaphone1": S.metaphone(last_std) or "",
                "last_name_metaphone2": "",
                "last_name_transposed": "",
                "last_name_suffix": "",
                "last_initial": (last_name[:1] if last_name else ""),
                "last_name8": (last_std or "")[:8],
                "full_name_std": full_name_std,
                "full_name_metaphone": S.metaphone(full_name_std) or "",
                "full_name_transposed": "",
                "full_name_dob": f"{full_name_std}{birth_date}" if birth_date else "",
                "birth_month": bm,
                "birth_day": bd,
                "birth_year": by,
                "ssn4": (ssn[-4:] if ssn else ""),
                "address_source": ("ride_enrollment" if has_address else ""),
                "address1": address1,
                "address2": "",
                "city": city,
                "state": ("RI" if has_address else ""),
                "zip": zip5,
            }
        )
    return rows


REFERENCE_FIELDNAMES = [
    "idcol_id", "person_id", "dataset_id",
    "first_name", "middle_name", "last_name", "birth_date", "gender", "ssn",
    "apprentice_id", "brown_id", "bryant_id", "ccri_id", "college_board_id",
    "dcyf_id", "dlt_ern", "employri_id", "ged_id", "jwu_id", "kidsnet_child_id",
    "laces_id", "laces_staff_id", "laces_student_id", "lasid", "nspid", "ods",
    "ric_id", "ride_cert_id", "ridoh_lead_id", "risd_id", "rjri_id", "rwu_id",
    "salve_id", "sasid", "uri_id", "voter_id", "workforce_id",
    "providencecollege_id", "netech_id",
    "first_name_std", "first_name_metaphone1", "first_name_metaphone2",
    "first_name_transposed", "first_initial", "middle_name_std", "middle_initial",
    "last_name_std", "last_name_metaphone1", "last_name_metaphone2",
    "last_name_transposed", "last_name_suffix", "last_initial", "last_name8",
    "full_name_std", "full_name_metaphone", "full_name_transposed", "full_name_dob",
    "birth_month", "birth_day", "birth_year", "ssn4",
    "address_source", "address1", "address2", "city", "state", "zip",
]


def write_reference_csv(rows: list[dict]) -> None:
    path = OUT / "rilds_reference_100k.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=REFERENCE_FIELDNAMES)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {path} ({len(rows):,} rows)")


def write_input_csv(name: str, header: list[str], rows: list[list[str]]) -> None:
    path = OUT / f"ride_enrollment_{name}.csv"
    with path.open("w", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow(header)
        w.writerows(rows)
    people = len({r[5] for r in rows if len(r) > 5})
    print(f"  wrote {path}  ({len(rows):,} rows, {people:,} unique people)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", required=True, type=Path,
        help="Path to the real RIDE enrollment extract (not committed to the repo).",
    )
    args = parser.parse_args()
    if not args.source.exists():
        raise SystemExit(f"Source file not found: {args.source}")

    from matchbot.config.loader import load_config

    rng = random.Random(SEED)
    fake = Faker()
    Faker.seed(SEED)
    OUT.mkdir(parents=True, exist_ok=True)

    config = load_config(Path("config"))
    std_config = config.global_config.standardization

    print(f"Reading distinct SASIDs from {args.source} ...")
    people = _read_distinct_sasid_names(args.source, REFERENCE_SIZE, rng)
    print(f"  found {len(people):,} distinct people, sampling {REFERENCE_SIZE:,}")

    print("Building rilds_reference rows ...")
    rows = build_reference_rows(people, std_config, fake, rng)
    write_reference_csv(rows)

    print("Sampling real-data RIDE input files ...")
    for name, n in INPUT_SIZES.items():
        header, sampled = _sample_input_rows(args.source, n, rng)
        write_input_csv(name, header, sampled)

    print("Done.")


if __name__ == "__main__":
    main()
