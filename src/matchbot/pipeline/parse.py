"""Stage 1 — Parse.

Reads a provider file into a Polars DataFrame using a reader selected by the
provider's declared format. Readers are kept tiny and registered by
:class:`~matchbot.domain.enums.FileFormat`, so adding a new format is a new
function + registry entry — no change to the stage or orchestrator.

All columns are read as strings; type coercion happens in the cleanse stage,
driven by config. A ``source_row_id`` is attached for provenance/audit.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from typing import TYPE_CHECKING

import polars as pl

from matchbot.domain.enums import FileFormat, Stage
from matchbot.logging_setup import get_logger
from matchbot.pipeline.base import PipelineContext, StageResult

if TYPE_CHECKING:
    from matchbot.config.models import ProviderConfig

log = get_logger(__name__)

# format -> (raw_bytes, ProviderConfig) -> DataFrame
ReaderFn = Callable[[bytes, "ProviderConfig"], pl.DataFrame]
_READERS: dict[FileFormat, ReaderFn] = {}


def register_reader(fmt: FileFormat) -> Callable[[ReaderFn], ReaderFn]:
    def _wrap(fn: ReaderFn) -> ReaderFn:
        _READERS[fmt] = fn
        return fn

    return _wrap


@register_reader(FileFormat.CSV)
def _read_csv(data: bytes, provider: ProviderConfig) -> pl.DataFrame:
    return pl.read_csv(
        io.BytesIO(data),
        separator=provider.delimiter,
        has_header=provider.has_header,
        infer_schema_length=0,  # everything as Utf8; cleanse coerces
        truncate_ragged_lines=True,
    )


@register_reader(FileFormat.XLSX)
def _read_xlsx(data: bytes, provider: ProviderConfig) -> pl.DataFrame:
    sheet = provider.sheet_name if provider.sheet_name is not None else 0
    if isinstance(sheet, str):
        df = pl.read_excel(io.BytesIO(data), sheet_name=sheet)
    else:
        df = pl.read_excel(io.BytesIO(data), sheet_id=sheet + 1)
    # Normalize every column to Utf8 for uniform downstream handling.
    return df.with_columns(pl.all().cast(pl.Utf8, strict=False))


@register_reader(FileFormat.FIXED_WIDTH)
def _read_fixed_width(data: bytes, provider: ProviderConfig) -> pl.DataFrame:
    text = data.decode("utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    cols = provider.fixed_width_columns
    records: dict[str, list[str]] = {c.name: [] for c in cols}
    for line in lines:
        for c in cols:
            records[c.name].append(line[c.start : c.start + c.length].strip())
    return pl.DataFrame(records)


class ParseStage:
    """Read the raw file bytes into a typed-by-string Polars frame."""

    stage = Stage.PARSE

    def __init__(self, raw_bytes: bytes) -> None:
        self._raw = raw_bytes

    def run(self, ctx: PipelineContext, frame: pl.DataFrame) -> StageResult:
        reader = _READERS.get(ctx.provider.format)
        if reader is None:
            raise ValueError(f"No reader for format {ctx.provider.format!r}")
        df = reader(self._raw, ctx.provider)
        # Attach provenance.
        df = df.with_columns(
            pl.arange(0, df.height).alias("source_row_id"),
            pl.lit(ctx.provider.provider_id).alias("provider_id"),
            pl.lit(ctx.run_id).alias("run_id"),
        )
        log.info("parse.done", rows=df.height, columns=df.width, fmt=ctx.provider.format.value)
        return StageResult(frame=df)
