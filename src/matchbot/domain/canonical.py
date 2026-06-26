"""The canonical attribute dictionary.

This is the *shape* of a record once mapped from any provider into MatchBot's
common vocabulary. The set of canonical attributes is intentionally small and
stable; provider-specific columns are mapped onto these via per-provider
config. The authoritative list lives in ``config/global.yaml`` and is validated
against this module on load, so the two can never silently drift.

Inferred from the legacy system's CoreIdentifiers / DerivedIdentifiers and the
reference architecture's STAGE union (FN, LN, DOB, SSN, ADDR1, ADDR2, CITY,
STATE, ZIP + provider_id, source_row_id).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CanonicalAttribute:
    """A single field in the canonical schema.

    Parameters
    ----------
    name:
        The canonical attribute name (what providers map *to*).
    dtype:
        Logical type: ``string``, ``date``, or ``integer``. Used by the cleanse
        and canonical stages to coerce values consistently.
    description:
        Human-readable purpose, surfaced in docs / errors.
    pii:
        Whether the attribute carries PII. Used to drive redaction in logs.
    """

    name: str
    dtype: str
    description: str
    pii: bool = False


# The canonical identity attributes. Provider files are mapped onto this set.
# Adding a new canonical attribute is a deliberate, reviewed change here +
# config; it is NOT something a provider onboarding should require.
CANONICAL_ATTRIBUTES: tuple[CanonicalAttribute, ...] = (
    CanonicalAttribute("first_name", "string", "Given name", pii=True),
    CanonicalAttribute("middle_name", "string", "Middle name", pii=True),
    CanonicalAttribute("last_name", "string", "Family name", pii=True),
    CanonicalAttribute("birth_date", "date", "Date of birth", pii=True),
    CanonicalAttribute("ssn", "string", "Social Security Number", pii=True),
    CanonicalAttribute("gender", "string", "Gender (standardized)", pii=True),
    CanonicalAttribute("address1", "string", "Address line 1", pii=True),
    CanonicalAttribute("address2", "string", "Address line 2", pii=True),
    CanonicalAttribute("city", "string", "City", pii=False),
    CanonicalAttribute("state", "string", "State / region", pii=False),
    CanonicalAttribute("zip", "string", "Postal code", pii=False),
    CanonicalAttribute(
        "member_external_id",
        "string",
        "Provider-assigned strong person id (e.g. RIDE SASID, agency case id)",
        pii=True,
    ),
)

# Fast lookups.
CANONICAL_BY_NAME: dict[str, CanonicalAttribute] = {a.name: a for a in CANONICAL_ATTRIBUTES}
CANONICAL_NAMES: frozenset[str] = frozenset(CANONICAL_BY_NAME)

# Provenance columns attached to every staged record (not provider-mapped).
PROVENANCE_COLUMNS: tuple[str, ...] = ("provider_id", "source_row_id", "run_id")

# The full set of matching-attribute columns carried on stage / target / error
# rows: core identity + derived blocking fields + provider-specific ids. Kept in
# sync with storage.schema._identity_columns() — that builds the DB columns,
# this names them for denormalizing onto target/error so each row shows how it
# matched or failed.
MATCH_ATTRIBUTE_COLUMNS: tuple[str, ...] = (
    "first_name",
    "middle_name",
    "last_name",
    "birth_date",
    "gender",
    "first_name_std",
    "last_name_std",
    "first_name_metaphone",
    "last_name_metaphone",
    "last_name8",
    "birth_year",
    "birth_month",
    "birth_day",
    "sasid",
    "lasid",
)


def is_canonical(name: str) -> bool:
    """Return True if ``name`` is a known canonical attribute."""
    return name in CANONICAL_NAMES
