from __future__ import annotations

import datetime as dt
import time
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


def _fetch_with_retry(adapter, sku: str, zone: str, attempts: int = 3) -> dict | None:
    """Return the product detail, or None if the SKU is gone.

    Transient failures (timeouts, dropped connections, 5xx) are retried with
    backoff: a single flaky request must not abort a run of thousands.
    """
    for attempt in range(attempts):
        try:
            return adapter.get_product(sku, zone)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code < 500:
                return None  # Delisted between snapshot and enrichment.
            last = exc
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            last = exc
        if attempt < attempts - 1:
            time.sleep(2**attempt)
    raise last


def enrich_catalog(
    chain: str,
    zone: str,
    prices_dir: Path,
    catalog_path: Path,
    limit: int | None = None,
    checkpoint_every: int = 100,
) -> tuple[int, int]:
    """Fill the per-SKU catalog (EAN, brand, origin) from the product detail endpoint.

    Incremental by design: SKUs already in the catalog are never re-fetched, so
    the full catalog can be built across several slow, polite runs. Progress is
    flushed every `checkpoint_every` products, so an interrupted run keeps what
    it already fetched. Returns (fetched_now, still_missing).
    """
    adapter = ADAPTERS[chain]()
    catalog = load_catalog(catalog_path)
    known = set(catalog.filter(pl.col("chain") == chain)["sku"].to_list())

    prices = pl.scan_parquet(prices_dir / f"chain={chain}" / "**" / "*.parquet")
    skus = prices.select(pl.col("sku").unique().sort()).collect()["sku"].to_list()
    missing = [s for s in skus if s not in known]
    batch = missing if limit is None else missing[:limit]

    today = dt.datetime.now(tz=dt.UTC).date().isoformat()
    rows: list[dict] = []
    fetched = 0

    def flush() -> None:
        nonlocal catalog, rows
        if not rows:
            return
        catalog = pl.concat([catalog, pl.DataFrame(rows, schema=CATALOG_SCHEMA)])
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_parquet(catalog_path)
        rows = []

    try:
        for sku in batch:
            raw = _fetch_with_retry(adapter, sku, zone)
            if raw is None:
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
            fetched += 1
            if len(rows) >= checkpoint_every:
                flush()
    finally:
        flush()
    return fetched, len(missing) - fetched
