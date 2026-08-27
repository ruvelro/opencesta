from __future__ import annotations

from pathlib import Path

import polars as pl


def available_dates(prices_dir: Path, chain: str, zone: str) -> list[str]:
    lf = pl.scan_parquet(prices_dir / f"chain={chain}" / f"zone={zone}" / "**" / "*.parquet")
    return sorted(lf.select(pl.col("captured_at").unique()).collect()["captured_at"].to_list())


def diff(
    prices_dir: Path,
    chain: str,
    zone: str,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, pl.DataFrame | str]:
    """Compare two snapshots of one chain/zone: price moves, new and delisted SKUs.

    Defaults to the oldest and newest snapshots available.
    """
    dates = available_dates(prices_dir, chain, zone)
    if len(dates) < 2:
        raise ValueError(f"need 2+ snapshots for {chain}/{zone}, found {len(dates)}")
    since = since or dates[0]
    until = until or dates[-1]
    for label, value in (("since", since), ("until", until)):
        if value not in dates:
            raise ValueError(f"{label}={value!r} not in snapshots: {', '.join(dates)}")

    lf = pl.scan_parquet(prices_dir / f"chain={chain}" / f"zone={zone}" / "**" / "*.parquet")
    frame = (
        lf.filter(pl.col("captured_at").is_in([since, until]))
        .select("sku", "display_name", "category", "captured_at", "unit_price")
        .collect()
    )
    wide = frame.pivot(
        on="captured_at", index=["sku", "display_name", "category"], values="unit_price"
    )
    old, new = pl.col(since), pl.col(until)

    changed = (
        wide.drop_nulls([since, until])
        .with_columns(
            (new - old).round(2).alias("delta"),
            ((new - old) / old * 100).round(1).alias("pct"),
        )
        .filter(pl.col("delta") != 0)
        .sort("pct")
    )
    basket_pct = None
    both = wide.drop_nulls([since, until])
    if both.height:
        totals = both.select(pl.col(since).sum(), pl.col(until).sum()).row(0)
        basket_pct = round((totals[1] - totals[0]) / totals[0] * 100, 2)

    return {
        "since": since,
        "until": until,
        "changed": changed,
        "added": wide.filter(old.is_null()).select("sku", "display_name", until),
        "removed": wide.filter(new.is_null()).select("sku", "display_name", since),
        "tracked": both.height,
        "basket_pct": basket_pct,
    }
