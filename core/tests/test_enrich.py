import dataclasses

import httpx
import polars as pl
import pytest

from opencesta.enrich import enrich_catalog, load_catalog
from opencesta.models import PriceRecord


def write_prices(tmp_path, skus):
    records = [
        dataclasses.asdict(
            PriceRecord(
                chain="mercadona", zone="vlc1", sku=sku, display_name=f"p{sku}",
                category="c", subcategory="s", unit_price=1.0, reference_price=1.0,
                reference_format="L", unit_size=1.0, size_format="l", tax_pct=4.0,
                is_pack=False, is_discounted=False, url="", captured_at="2026-08-27",
            )
        )
        for sku in skus
    ]
    target = tmp_path / "chain=mercadona" / "zone=vlc1" / "date=2026-08-27"
    target.mkdir(parents=True)
    pl.DataFrame(records).select(PriceRecord.columns()).write_parquet(target / "prices.parquet")


@pytest.fixture
def patched_adapter(monkeypatch):
    """Serve product details from a dict, recording which SKUs were fetched."""

    def install(details: dict, fetched: list):
        def fake_get_product(self, sku, zone):
            fetched.append(sku)
            if sku not in details:
                raise httpx.HTTPStatusError(
                    "404", request=httpx.Request("GET", "http://x"),
                    response=httpx.Response(404),
                )
            return details[sku]

        monkeypatch.setattr(
            "opencesta.adapters.mercadona.MercadonaAdapter.get_product", fake_get_product
        )

    return install


def detail(sku, ean="8402001027482", brand="Hacendado"):
    return {"id": sku, "ean": ean, "brand": brand, "origin": "España",
            "packaging": "Garrafa", "details": {"legal_name": f"legal {sku}"}}


def test_enrich_writes_catalog(tmp_path, patched_adapter):
    write_prices(tmp_path, ["1", "2"])
    patched_adapter({"1": detail("1"), "2": detail("2", ean="123", brand="Dia")}, [])
    catalog_path = tmp_path / "catalog" / "mercadona.parquet"

    fetched, missing = enrich_catalog("mercadona", "vlc1", tmp_path, catalog_path)
    assert (fetched, missing) == (2, 0)

    catalog = load_catalog(catalog_path).sort("sku")
    assert catalog["ean"].to_list() == ["8402001027482", "123"]
    assert catalog["brand"].to_list() == ["Hacendado", "Dia"]
    assert catalog["legal_name"].to_list() == ["legal 1", "legal 2"]


def test_enrich_is_incremental(tmp_path, patched_adapter):
    write_prices(tmp_path, ["1", "2", "3"])
    calls: list[str] = []
    patched_adapter({s: detail(s) for s in ("1", "2", "3")}, calls)
    catalog_path = tmp_path / "catalog" / "mercadona.parquet"

    assert enrich_catalog("mercadona", "vlc1", tmp_path, catalog_path, limit=2) == (2, 1)
    assert calls == ["1", "2"]

    # Second run resumes: already-known SKUs are never refetched.
    calls.clear()
    assert enrich_catalog("mercadona", "vlc1", tmp_path, catalog_path) == (1, 0)
    assert calls == ["3"]
    assert sorted(load_catalog(catalog_path)["sku"].to_list()) == ["1", "2", "3"]


def test_enrich_retries_transient_failure(tmp_path, monkeypatch, patched_adapter):
    write_prices(tmp_path, ["1"])
    monkeypatch.setattr("opencesta.enrich.time.sleep", lambda _: None)
    attempts = []

    def flaky(self, sku, zone):
        attempts.append(sku)
        if len(attempts) < 3:
            raise httpx.ReadTimeout("boom")
        return detail(sku)

    monkeypatch.setattr("opencesta.adapters.mercadona.MercadonaAdapter.get_product", flaky)
    catalog_path = tmp_path / "catalog" / "mercadona.parquet"

    assert enrich_catalog("mercadona", "vlc1", tmp_path, catalog_path) == (1, 0)
    assert len(attempts) == 3


def test_enrich_keeps_progress_when_run_dies(tmp_path, monkeypatch, patched_adapter):
    """A timeout mid-run must not throw away everything fetched before it."""
    write_prices(tmp_path, ["1", "2", "3", "4"])
    monkeypatch.setattr("opencesta.enrich.time.sleep", lambda _: None)

    def dies_on_third(self, sku, zone):
        if sku == "3":
            raise httpx.ReadTimeout("boom")
        return detail(sku)

    monkeypatch.setattr("opencesta.adapters.mercadona.MercadonaAdapter.get_product", dies_on_third)
    catalog_path = tmp_path / "catalog" / "mercadona.parquet"

    with pytest.raises(httpx.ReadTimeout):
        enrich_catalog("mercadona", "vlc1", tmp_path, catalog_path, checkpoint_every=1)

    assert sorted(load_catalog(catalog_path)["sku"].to_list()) == ["1", "2"]


def test_enrich_skips_delisted_sku(tmp_path, patched_adapter):
    write_prices(tmp_path, ["1", "gone"])
    patched_adapter({"1": detail("1")}, [])
    catalog_path = tmp_path / "catalog" / "mercadona.parquet"

    fetched, missing = enrich_catalog("mercadona", "vlc1", tmp_path, catalog_path)
    assert (fetched, missing) == (1, 1)
    assert load_catalog(catalog_path)["sku"].to_list() == ["1"]


def test_only_the_requested_zone_is_enriched(tmp_path, patched_adapter):
    """A SKU sold only in another zone 404s here and would be re-asked forever."""
    write_prices(tmp_path, ["1"])  # zone vlc1
    other = tmp_path / "chain=mercadona" / "zone=mad1" / "date=2026-08-27"
    other.mkdir(parents=True)
    records = [
        dataclasses.asdict(
            PriceRecord(
                chain="mercadona", zone="mad1", sku="solo-mad1", display_name="p",
                category="c", subcategory="s", unit_price=1.0, reference_price=1.0,
                reference_format="L", unit_size=1.0, size_format="l", tax_pct=4.0,
                is_pack=False, is_discounted=False, url="", captured_at="2026-08-27",
            )
        )
    ]
    pl.DataFrame(records).select(PriceRecord.columns()).write_parquet(
        other / "prices.parquet"
    )

    asked: list[str] = []
    patched_adapter({"1": detail("1")}, asked)
    fetched, missing = enrich_catalog(
        "mercadona", "vlc1", tmp_path, tmp_path / "catalog" / "mercadona.parquet"
    )

    assert asked == ["1"]  # the mad1-only SKU is never requested from vlc1
    assert (fetched, missing) == (1, 0)
