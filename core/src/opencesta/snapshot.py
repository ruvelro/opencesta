from __future__ import annotations

import dataclasses
import datetime as dt
from pathlib import Path

import polars as pl

from opencesta.adapters import ADAPTERS
from opencesta.models import SCHEMA, PriceRecord


def snapshot(chain: str, zone: str, out_dir: Path, captured_at: str | None = None) -> Path:
    """Capture the full catalog of one chain/zone and write a partitioned Parquet.

    Layout: out_dir/chain=<chain>/zone=<zone>/date=<date>/prices.parquet
    """
    started = dt.datetime.now(tz=dt.UTC)
    captured_at = captured_at or started.date().isoformat()
    adapter = ADAPTERS[chain]()
    records = list(adapter.iter_products(zone, captured_at))
    if not records:
        raise RuntimeError(f"snapshot for {chain}/{zone} produced 0 products")
    # One timestamp per capture, not per row: the walk takes minutes and what a
    # consumer needs is "when did this snapshot run", not a per-request clock.
    stamp = started.isoformat(timespec="seconds")
    records = [dataclasses.replace(r, captured_at_ts=stamp) for r in records]

    df = pl.DataFrame([dataclasses.asdict(r) for r in records], schema=SCHEMA).select(
        PriceRecord.columns()
    )

    target = out_dir / f"chain={chain}" / f"zone={zone}" / f"date={captured_at}"
    target.mkdir(parents=True, exist_ok=True)
    path = target / "prices.parquet"
    df.write_parquet(path)
    return path
