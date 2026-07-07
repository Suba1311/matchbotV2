"""Stage 1 — Parse.

Reads a provider file into a Polars DataFrame using a reader selected by the
provider's declared format. Readers are kept tiny and registered by
:class:`~matchbot.domain.enums.FileFormat`, so adding a new format is a new
function + registry entry — no change to the stage or orchestrator.

All columns are read as strings; type coercion happens in the cleanse stage,
driven by config. A ``source_row_id`` is attached for provenance/audit.

Real source extracts sometimes have rows with unescaped commas inside
unquoted fields (e.g. a high-school name), which shifts every column after
the extra comma. Polars' ``truncate_ragged_lines=True`` does NOT reject these
— it silently drops trailing fields and lets the row through *shifted*, so a
column can end up holding an entirely different field's data with no error
raised. CSV rows are therefore field-count-validated with Python's own
``csv`` module (which respects quoting correctly) before Polars ever sees the
bytes; anything that doesn't match the header's field count is routed to
``rilds_land_rejects`` as a verbatim raw line, and only clean rows are parsed.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import polars as pl

from matchbot.domain.enums import FileFormat, Stage
from matchbot.logging_setup import get_logger
from matchbot.pipeline.base import PipelineContext, StageResult

if TYPE_CHECKING:
    from matchbot.config.models import ProviderConfig

log = get_logger(__name__)

# format -> (raw_bytes, ProviderConfig) -> (DataFrame, rejected raw lines)
ReaderFn = Callable[[bytes, "ProviderConfig"], "tuple[pl.DataFrame, list[dict[str, Any]]]"]
_READERS: dict[FileFormat, ReaderFn] = {}


def register_reader(fmt: FileFormat) -> Callable[[ReaderFn], ReaderFn]:
    def _wrap(fn: ReaderFn) -> ReaderFn:
        _READERS[fmt] = fn
        return fn

    return _wrap


@register_reader(FileFormat.CSV)
def _read_csv(data: bytes, provider: ProviderConfig) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    # Streamed via TextIOWrapper (not data.decode(...) up front) so the whole
    # file is never held as one Python string. Clean rows are written straight
    # to the output buffer as they're seen — never collected into a Python
    # list first — so peak memory is roughly one row plus the two buffers
    # (input text stream, output clean-csv stream), not multiple full-file
    # copies. This matters at real scale: a naive "collect clean rows into a
    # list, then re-serialize" approach reintroduces the same OOM risk class
    # fixed earlier for LAND/MATCH (see docs/glue-implementation.md).
    src = io.TextIOWrapper(io.BytesIO(data), encoding="utf-8-sig", errors="replace")
    reader = csv.reader(src, delimiter=provider.delimiter)

    clean_buf = io.StringIO()
    writer = csv.writer(clean_buf, delimiter=provider.delimiter)

    header: list[str] | None = None
    expected: int | None = None
    rejects: list[dict[str, Any]] = []

    for fields in reader:
        if provider.has_header and header is None:
            header = fields
            expected = len(fields)
            writer.writerow(header)
            continue
        # The first data row establishes the expected width when there's no
        # header (has_header=False) — every row (including this first one)
        # is still validated against it, unlike a header row, which is
        # trusted as-is and never itself subject to rejection. The synthetic
        # header can only be written once we know this width, i.e. now.
        if expected is None:
            expected = len(fields)
            header = [f"column_{i}" for i in range(expected)]
            writer.writerow(header)
        if len(fields) != expected:
            raw_line = provider.delimiter.join(fields)
            rejects.append(
                {
                    "raw_line": raw_line,
                    "reason": f"field_count_mismatch: expected {expected}, got {len(fields)}",
                }
            )
            continue
        writer.writerow(fields)

    df = pl.read_csv(
        clean_buf.getvalue().encode("utf-8"),
        separator=provider.delimiter,
        has_header=True,
        infer_schema_length=0,  # everything as Utf8; cleanse coerces
    )
    if rejects:
        log.warning(
            "parse.ragged_rows_rejected",
            count=len(rejects),
            provider=provider.provider_id,
        )
    return df, rejects


@register_reader(FileFormat.XLSX)
def _read_xlsx(data: bytes, provider: ProviderConfig) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    sheet = provider.sheet_name if provider.sheet_name is not None else 0
    if isinstance(sheet, str):
        df = pl.read_excel(io.BytesIO(data), sheet_name=sheet)
    else:
        df = pl.read_excel(io.BytesIO(data), sheet_id=sheet + 1)
    # Normalize every column to Utf8 for uniform downstream handling.
    df = df.with_columns(pl.all().cast(pl.Utf8, strict=False))
    return df, []


@register_reader(FileFormat.FIXED_WIDTH)
def _read_fixed_width(
    data: bytes, provider: ProviderConfig
) -> tuple[pl.DataFrame, list[dict[str, Any]]]:
    text = data.decode("utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    cols = provider.fixed_width_columns
    records: dict[str, list[str]] = {c.name: [] for c in cols}
    for line in lines:
        for c in cols:
            records[c.name].append(line[c.start : c.start + c.length].strip())
    return pl.DataFrame(records), []


class ParseStage:
    """Read the raw file bytes into a typed-by-string Polars frame."""

    stage = Stage.PARSE

    def __init__(self, raw_bytes: bytes) -> None:
        self._raw = raw_bytes

    def run(self, ctx: PipelineContext, frame: pl.DataFrame) -> StageResult:
        reader = _READERS.get(ctx.provider.format)
        if reader is None:
            raise ValueError(f"No reader for format {ctx.provider.format!r}")
        df, rejects = reader(self._raw, ctx.provider)
        # Attach provenance.
        df = df.with_columns(
            pl.arange(0, df.height).alias("source_row_id"),
            pl.lit(ctx.provider.provider_id).alias("provider_id"),
            pl.lit(ctx.run_id).alias("run_id"),
        )
        log.info(
            "parse.done",
            rows=df.height,
            columns=df.width,
            fmt=ctx.provider.format.value,
            rejected=len(rejects),
        )
        return StageResult(frame=df, side_outputs={"land_rejects": rejects})
