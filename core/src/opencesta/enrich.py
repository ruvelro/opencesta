from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx
import polars as pl

from opencesta.adapters import ADAPTERS

CATALOG_SCHEMA = {
    "chain": pl.String,
    "sku": pl.String,
    "ean": pl.String,
    "brand": pl.String,
    "origin": pl.String,
    "packaging": pl.String,
    "legal_name": pl.String,
    "enriched_at": pl.String,
}


def load_catalog(path: Path) -> pl.DataFrame:
    if path.exists():
        return pl.read_parquet(path)
    return pl.DataFrame(schema=CATALOG_SCHEMA)


def enrich_catalog(
    chain: str,
    zone: str,
    prices_dir: Path,
    catalog_path: Path,
    limit: int | None = None,
) -> tuple[int, int]:
    """Fill the per-SKU catalog (EAN, brand, origin) from the product detail endpoint.

    Incremental by design: SKUs already in the catalog are never re-fetched, so
    the full catalog can be built across several slow, polite runs. Returns
    (fetched_now, still_missing).
    """
    adapter = ADAPTERS[chain]()
    catalog = load_catalog(catalog_path)
    known = set(catalog.filter(pl.col("chain") == chain)["sku"].to_list())

    prices = pl.scan_parquet(prices_dir / f"chain={chain}" / "**" / "*.parquet")
    skus = prices.select(pl.col("sku").unique().sort()).collect()["sku"].to_list()
    missing = [s for s in skus if s not in known]
    batch = missing if limit is None else missing[:limit]

    today = dt.datetime.now(tz=dt.UTC).date().isoformat()
    rows = []
    for sku in batch:
        try:
            raw = adapter.get_product(sku, zone)
        except httpx.HTTPStatusError:
            # Delisted between snapshot and enrichment: skip, retry next run.
            continue
        details = raw.get("details") or {}
        rows.append(
            {
                "chain": chain,
                "sku": str(raw["id"]),
                "ean": str(raw["ean"]) if raw.get("ean") else None,
                "brand": raw.get("brand"),
                "origin": raw.get("origin"),
                "packaging": raw.get("packaging"),
                "legal_name": details.get("legal_name"),
                "enriched_at": today,
            }
        )

    if rows:
        catalog = pl.concat([catalog, pl.DataFrame(rows, schema=CATALOG_SCHEMA)])
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_parquet(catalog_path)
    return len(rows), len(missing) - len(rows)
