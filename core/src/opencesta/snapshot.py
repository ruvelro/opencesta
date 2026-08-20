from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

import polars as pl

from opencesta.adapters import ADAPTERS
from opencesta.models import PriceRecord


def snapshot(chain: str, zone: str, out_dir: Path, captured_at: str | None = None) -> Path:
    """Capture the full catalog of one chain/zone and write a partitioned Parquet.

    Layout: out_dir/chain=<chain>/zone=<zone>/date=<date>/prices.parquet
    """
    captured_at = captured_at or dt.datetime.now(tz=dt.UTC).date().isoformat()
    adapter = ADAPTERS[chain]()
    records = list(adapter.iter_products(zone, captured_at))
    if not records:
        raise RuntimeError(f"snapshot for {chain}/{zone} produced 0 products")

    df = pl.DataFrame(
        [dataclasses.asdict(r) for r in records],
        schema_overrides={c: pl.Float64 for c in
                          ("unit_price", "reference_price", "unit_size", "tax_pct")},
    ).select(PriceRecord.columns())

    target = out_dir / f"chain={chain}" / f"zone={zone}" / f"date={captured_at}"
    target.mkdir(parents=True, exist_ok=True)
    path = target / "prices.parquet"
    df.write_parquet(path)
    return path
