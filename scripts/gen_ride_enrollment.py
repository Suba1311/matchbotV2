"""Generate dummy RIDE Enrollment provider files matching the real schema.

Produces ride_enrollment_1k.csv / _10k.csv / _100k.csv under data/samples/,
faithfully reproducing the real file's 36-column layout and quirks:

* ``RECID``  is unique per row (one per enrollment record).
* ``SASID``  is the person key — a person may have several enrollment rows
  (different colleges), exactly like the sample (Tom Hanks had 3).
* Literal ``NULL`` strings, ``00:00.0`` time placeholders, and a trailing
  comma (the sample's 37th empty field) are reproduced.
* Values containing commas (e.g. ``WEST HILLS COLLEGE, LEMOORE``) are written
  with proper CSV quoting so the file stays valid while still looking real.

Counts mean ROWS, not unique people. Run:
    uv run python scripts/gen_ride_enrollment.py
"""

from __future__ import annotations

import csv
import random
from pathlib import Path

from faker import Faker

OUT = Path("data/samples")
SEED = 2024
SIZES = {"1k": 1_000, "10k": 10_000, "100k": 100_000}

# Exact header from RIDE_Enrollment_sample.csv (note the trailing empty column).
HEADER = [
    "RECID", "FIRSTNAME", "MIDDLENAME", "LASTNAME", "NAMESUFFIX", "SASID",
    "RECORDFOUND", "SEARCHDATE", "COLLEGECODE", "COLLEGENAME", "COLLEGESTATE",
    "COLLEGETYPE", "PUBLICPRIVATE", "ENROLLBEGIN", "ENROLLEND", "ENROLLSTATUS",
    "GRADUATED", "GRADUATIONDATE", "DEGREETITLE", "MAJOR", "COLLEGESEQ",
    "CREDIT", "ENROLLFIRST", "ENROLLLAST", "HSGRADDATE", "HSEXITTYPE", "HSCODE",
    "HSNAME", "DISTCODE", "DISTNAME", "SEX", "RACE", "RACE7", "LUNCH", "IEP",
    "LEP", "",
]

# Realistic-looking lookup pools drawn from the sample's conventions.
COLLEGES = [
    ("003508-00", "COMMUNITY COLLEGE OF CONNECTICUT", "RI", "2-year", "Public"),
    ("041713-00", "WEST HILLS COLLEGE, LEMOORE", "CA", "2-year", "Public"),
    ("001276-00", "WEST HILLS COMMUNITY COLLEGE", "CA", "2-year", "Public"),
    ("003401-00", "UNIVERSITY OF RHODE ISLAND", "RI", "4-year", "Public"),
    ("003407-00", "RHODE ISLAND COLLEGE", "RI", "4-year", "Public"),
    ("003404-00", "BROWN UNIVERSITY", "RI", "4-year", "Private"),
    ("002120-00", "BRYANT UNIVERSITY", "RI", "4-year", "Private"),
    ("007468-00", "COMMUNITY COLLEGE OF RHODE ISLAND", "RI", "2-year", "Public"),
    ("003410-00", "PROVIDENCE COLLEGE", "RI", "4-year", "Private"),
    ("003414-00", "SALVE REGINA UNIVERSITY", "RI", "4-year", "Private"),
    ("009823-00", "JOHNSON & WALES UNIVERSITY", "RI", "4-year", "Private"),
]
HIGH_SCHOOLS = [
    ("96107", "Mt. Hope High School", "96", "Bristol Warren"),
    ("12005", "Classical High School", "12", "Providence"),
    ("28010", "Cranston High School East", "28", "Cranston"),
    ("41020", "La Salle Academy", "41", "Private"),
    ("33015", "Coventry High School", "33", "Coventry"),
    ("58030", "South Kingstown High School", "58", "South Kingstown"),
]
ENROLL_STATUS = ["Q", "H", "W", "F", "G", "A"]   # codes seen / plausible
RACE_CODES = ["E", "W", "B", "H", "A", "I"]
RACE7 = ["WH7", "BL7", "HI7", "AS7", "AI7", "MU7"]
SEX = ["M", "F"]
YN = ["Y", "N"]
HS_EXIT = ["15", "16", "17", "18"]
NULL = "NULL"
TIME0 = "00:00.0"


def _person(fake: Faker) -> dict:
    """One person's stable identity (shared across their enrollment rows)."""
    has_suffix = random.random() < 0.04
    has_middle = random.random() < 0.5
    hs = random.choice(HIGH_SCHOOLS)
    return {
        "FIRSTNAME": fake.first_name().upper(),
        "MIDDLENAME": fake.first_name().upper() if has_middle else "",
        "LASTNAME": fake.last_name().upper(),
        "NAMESUFFIX": random.choice(["JR", "SR", "III"]) if has_suffix else "",
        "SASID": str(fake.random_int(1_000_000_000, 1_999_999_999)),
        "SEX": random.choice(SEX),
        "RACE": random.choice(RACE_CODES),
        "RACE7": random.choice(RACE7),
        "LUNCH": random.choice(YN),
        "IEP": random.choice(YN),
        "LEP": random.choice(YN),
        "HSGRADDATE": TIME0,
        "HSEXITTYPE": random.choice(HS_EXIT),
        "HSCODE": hs[0],
        "HSNAME": hs[1],
        "DISTCODE": hs[2],
        "DISTNAME": hs[3],
    }


def _enrollment_row(recid: int, person: dict, seq: int) -> list[str]:
    """One enrollment record (row) for a person at a given college."""
    college = random.choice(COLLEGES)
    graduated = random.random() < 0.25
    return [
        str(recid),                              # RECID
        person["FIRSTNAME"],                     # FIRSTNAME
        person["MIDDLENAME"],                    # MIDDLENAME
        person["LASTNAME"],                      # LASTNAME
        person["NAMESUFFIX"],                    # NAMESUFFIX
        person["SASID"],                         # SASID
        "Y",                                     # RECORDFOUND
        NULL,                                    # SEARCHDATE
        college[0],                              # COLLEGECODE
        college[1],                              # COLLEGENAME (may contain comma)
        college[2],                              # COLLEGESTATE
        college[3],                              # COLLEGETYPE
        college[4],                              # PUBLICPRIVATE
        TIME0,                                   # ENROLLBEGIN
        TIME0,                                   # ENROLLEND
        random.choice(ENROLL_STATUS),            # ENROLLSTATUS
        "Y" if graduated else "N",               # GRADUATED
        TIME0 if graduated else NULL,            # GRADUATIONDATE
        "" if not graduated else random.choice(["AS", "BS", "BA"]),  # DEGREETITLE
        "" if not graduated else random.choice(["BIOLOGY", "NURSING", "BUSINESS"]),  # MAJOR
        str(seq),                                # COLLEGESEQ
        random.choice(["0", "0.25", "0.5", "1"]),  # CREDIT
        TIME0,                                   # ENROLLFIRST
        TIME0,                                   # ENROLLLAST
        TIME0,                                   # HSGRADDATE (col 25, time placeholder)
        person["HSEXITTYPE"],                    # HSEXITTYPE
        person["HSCODE"],                        # HSCODE
        person["HSNAME"],                        # HSNAME
        person["DISTCODE"],                      # DISTCODE
        person["DISTNAME"],                      # DISTNAME
        person["SEX"],                           # SEX
        person["RACE"],                          # RACE
        person["RACE7"],                         # RACE7
        person["LUNCH"],                         # LUNCH
        person["IEP"],                           # IEP
        person["LEP"],                           # LEP
        "",                                      # trailing empty column
    ]


def generate(n_rows: int, fake: Faker, start_recid: int) -> list[list[str]]:
    """Generate exactly ``n_rows`` enrollment rows across a pool of people."""
    rows: list[list[str]] = []
    recid = start_recid
    while len(rows) < n_rows:
        person = _person(fake)
        # A person contributes 1-4 enrollment rows (like the sample's 3).
        n_enroll = min(random.choices([1, 2, 3, 4], weights=[55, 25, 13, 7])[0],
                       n_rows - len(rows))
        for seq in range(1, n_enroll + 1):
            rows.append(_enrollment_row(recid, person, seq + 1))
            recid += 1
    return rows


def write_file(name: str, rows: list[list[str]]) -> None:
    path = OUT / f"ride_enrollment_{name}.csv"
    with path.open("w", newline="") as f:
        # QUOTE_MINIMAL keeps the file looking like the sample but quotes the
        # college names that contain commas, so the CSV stays valid.
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow(HEADER)
        w.writerows(rows)
    people = len({r[5] for r in rows})
    print(f"  wrote {path}  ({len(rows):,} rows, {people:,} unique people)")


def main() -> None:
    random.seed(SEED)
    fake = Faker()
    Faker.seed(SEED)
    OUT.mkdir(parents=True, exist_ok=True)
    print("Generating RIDE Enrollment dummy files in data/samples/ ...")
    recid = 900_000
    for name, n in SIZES.items():
        rows = generate(n, fake, recid)
        recid += len(rows) + 1000
        write_file(name, rows)
    print("Done.")


if __name__ == "__main__":
    main()
