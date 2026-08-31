"""Repair Parquet files written before the schema was declared.

Early snapshots inferred their dtypes per file, so a column a chain never fills
was stored as a Null column — and the published history could not be read as one
series. This casts those columns onto the declared schema.

It touches types only. Every value, row count and column order is preserved, and
a file whose schema is already correct is left untouched.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from opencesta.models import SCHEMA, PriceRecord


def retype_file(path: Path) -> list[str]:
    """Cast a file's Null columns onto the declared schema; return what was fixed."""
    frame = pl.read_parquet(path)
    broken = [name for name, dtype in frame.schema.items() if dtype == pl.Null]
    if not broken:
        return []
    unknown = [name for name in broken if name not in SCHEMA]
    if unknown:
        raise ValueError(f"{path}: columnas fuera del esquema: {', '.join(unknown)}")

    rows_before = frame.height
    repaired = frame.with_columns(
        [pl.col(name).cast(SCHEMA[name]) for name in broken]
    ).select(PriceRecord.columns())
    if repaired.height != rows_before:
        raise RuntimeError(f"{path}: la reparación cambió el número de filas")
    repaired.write_parquet(path)
    return broken


def retype_tree(root: Path) -> dict[Path, list[str]]:
    """Repair every Parquet under `root`, reporting only the files it changed."""
    fixed: dict[Path, list[str]] = {}
    for path in sorted(root.rglob("*.parquet")):
        broken = retype_file(path)
        if broken:
            fixed[path] = broken
    return fixed
