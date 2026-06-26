"""Generate synthetic provider files + a Member Universe seed (no real PII).

Produces, under ``data/samples/``:

* ``provider1_*.xlsx``  (Excel)
* ``provider2_*.csv``   (CSV)
* ``provider3_*.txt``   (fixed-width)
* ``member_universe.csv`` (seed for the read-only member master)

The data is deliberately constructed so a run exercises every routing path:
exact matches (in member universe), fuzzy/typo'd near-matches, and clear
non-matches. Run: ``uv run python scripts/gen_synthetic_data.py``.
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

from faker import Faker

OUT = Path("data/samples")
SEED = 42


def _members(fake: Faker, n: int) -> list[dict]:
    members = []
    for i in range(n):
        members.append(
            {
                "member_id": f"M{i + 1:05d}",
                "first_name": fake.first_name().upper(),
                "middle_name": "",
                "last_name": fake.last_name().upper(),
                "birth_date": fake.date_of_birth(minimum_age=18, maximum_age=90).isoformat(),
                "ssn": f"{fake.random_int(100000000, 999999999)}",
                "gender": random.choice(["MALE", "FEMALE"]),
                "address1": fake.street_address().upper(),
                "address2": "",
                "city": fake.city().upper(),
                "state": fake.state_abbr(),
                "zip": fake.postcode()[:5],
            }
        )
    return members


def _typo(s: str) -> str:
    """Introduce a single-character typo to simulate dirty provider data."""
    if len(s) < 3:
        return s
    i = random.randrange(1, len(s) - 1)
    return s[:i] + random.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + s[i + 1 :]


def write_member_universe(members: list[dict]) -> None:
    path = OUT / "member_universe.csv"
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(members[0].keys()))
        w.writeheader()
        w.writerows(members)
    print(f"  wrote {path} ({len(members)} members)")


def write_provider2_csv(members: list[dict], new_people: list[dict]) -> None:
    """CSV provider — mix of exact matches, typo'd matches, and new people."""
    rows = []
    # exact matches
    for m in members[:30]:
        rows.append(_row_for(m))
    # typo'd (fuzzy) — last name typo, keep SSN to still match deterministically
    for m in members[30:45]:
        r = _row_for(m)
        r["FIRST_NAME"] = _typo(r["FIRST_NAME"])
        rows.append(r)
    # new people (no match)
    for m in new_people:
        rows.append(_row_for(m))

    random.shuffle(rows)
    path = OUT / "provider2_2024q1.csv"
    fieldnames = [
        "FIRST_NAME",
        "LAST_NAME",
        "DOB",
        "SSN",
        "GENDER",
        "ADDR1",
        "CITY",
        "STATE",
        "ZIP",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {path} ({len(rows)} rows)")


def _row_for(m: dict) -> dict:
    # DOB as m/d/Y per provider2 config; dashed SSN to test stripping.
    y, mo, d = m["birth_date"].split("-")
    ssn = m["ssn"].zfill(9)
    return {
        "FIRST_NAME": m["first_name"],
        "LAST_NAME": m["last_name"],
        "DOB": f"{int(mo)}/{int(d)}/{y}",
        "SSN": f"{ssn[:3]}-{ssn[3:5]}-{ssn[5:]}",
        "GENDER": m["gender"][0],
        "ADDR1": m["address1"],
        "CITY": m["city"],
        "STATE": m["state"],
        "ZIP": m["zip"],
    }


def write_provider1_xlsx(members: list[dict], new_people: list[dict]) -> None:
    try:
        import xlsxwriter
    except ImportError:
        print("  (skipping xlsx — install dev extra for xlsxwriter)")
        return
    path = OUT / "provider1_2024.xlsx"
    wb = xlsxwriter.Workbook(str(path))
    ws = wb.add_worksheet()
    headers = [
        "FirstName",
        "MiddleName",
        "LastName",
        "DOB",
        "SSN",
        "Gender",
        "Address",
        "Address2",
        "City",
        "State",
        "Zip",
    ]
    for c, h in enumerate(headers):
        ws.write(0, c, h)
    rows = []
    for m in members[:25]:
        rows.append(m)
    for m in new_people[:10]:
        rows.append(m)
    random.shuffle(rows)
    for r, m in enumerate(rows, start=1):
        ssn = m["ssn"].zfill(9)
        ws.write(r, 0, m["first_name"])
        ws.write(r, 1, m["middle_name"])
        ws.write(r, 2, m["last_name"])
        ws.write(r, 3, m["birth_date"])
        ws.write(r, 4, f"{ssn[:3]}-{ssn[3:5]}-{ssn[5:]}")
        ws.write(r, 5, m["gender"])
        ws.write(r, 6, m["address1"])
        ws.write(r, 7, m["address2"])
        ws.write(r, 8, m["city"])
        ws.write(r, 9, m["state"])
        ws.write(r, 10, m["zip"])
    wb.close()
    print(f"  wrote {path} ({len(rows)} rows)")


def write_provider3_fixed(members: list[dict], new_people: list[dict]) -> None:
    """Fixed-width per provider3 config slices."""

    def fmt(m: dict) -> str:
        y, mo, d = m["birth_date"].split("-")
        bdate = f"{y}{mo}{d}"
        return (
            f"{m['first_name']:<20.20}"
            f"{m['last_name']:<25.25}"
            f"{bdate:<8.8}"
            f"{m['ssn'].zfill(9):<9.9}"
            f"{m['gender'][0]:<1.1}"
            f"{m['city']:<20.20}"
            f"{m['state']:<2.2}"
            f"{m['zip']:<5.5}"
        )

    rows = [fmt(m) for m in members[:20]] + [fmt(m) for m in new_people[10:20]]
    random.shuffle(rows)
    path = OUT / "provider3_batch.txt"
    path.write_text("\n".join(rows) + "\n")
    print(f"  wrote {path} ({len(rows)} rows)")


def main() -> None:
    random.seed(SEED)
    fake = Faker()
    Faker.seed(SEED)
    OUT.mkdir(parents=True, exist_ok=True)

    members = _members(fake, 100)
    new_people = _members(fake, 30)  # people NOT in the member universe
    # ensure new_people SSNs don't collide with members
    member_ssns = {m["ssn"] for m in members}
    new_people = [m for m in new_people if m["ssn"] not in member_ssns]

    print("Generating synthetic data in data/samples/ ...")
    write_member_universe(members)
    write_provider2_csv(members, new_people)
    write_provider1_xlsx(members, new_people)
    write_provider3_fixed(members, new_people)
    print("Done.")


if __name__ == "__main__":
    main()





