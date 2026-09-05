"""The MCP tools are plain functions once registered; call them against a local dataset."""

import dataclasses
import json

import polars as pl
import pytest

from opencesta import mcp_server
from opencesta.models import PriceRecord


def snapshot(root, chain, zone, date, products):
    records = [dataclasses.asdict(PriceRecord(
        chain=chain, zone=zone, sku=sku, display_name=name, category="c", subcategory="s",
        unit_price=price, reference_price=price, reference_format="L", unit_size=1.0,
        size_format="l", tax_pct=4.0, is_pack=False, is_discounted=False, url="",
        captured_at=date, brand=brand,
    )) for sku, name, price, brand in products]
    target = root / "data" / f"chain={chain}" / f"zone={zone}" / f"date={date}"
    target.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(records).select(PriceRecord.columns()).write_parquet(target / "prices.parquet")


@pytest.fixture
def dataset(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCESTA_DATA", str(tmp_path))
    mcp_server._dataset.cache_clear()
    for date, milk_m, milk_d in (("2026-09-04", 1.00, 1.20), ("2026-09-05", 1.10, 1.20)):
        snapshot(tmp_path, "mercadona", "vlc1", date, [
            ("m1", "Leche semidesnatada Hacendado", milk_m, "Hacendado"),
            ("m2", "Aceite de oliva virgen extra Hacendado", 4.45, "Hacendado"),
            ("m3", "Solo en Mercadona", 9.0, "Hacendado"),
        ])
        snapshot(tmp_path, "dia", "es-default", date, [
            ("d1", "Leche semidesnatada Dia Láctea 1 L", milk_d, "Dia Láctea"),
            ("d2", "Aceite de oliva virgen extra Dia 1 L", 4.69, "Dia"),
        ])
    (tmp_path / "equivalences.jsonl").write_text("\n".join(json.dumps(e) for e in [
        {"a": {"sku": "m1"}, "b": {"sku": "d1"}, "method": "own-brand-equivalent"},
        {"a": {"sku": "m2"}, "b": {"sku": "d2"}, "method": "own-brand-equivalent"},
    ]) + "\n")
    return tmp_path


def test_status_resource_reports_the_loaded_dataset(dataset):
    status = json.loads(mcp_server.status())
    assert status["date"] == "2026-09-05"
    assert status["comparable_pairs"] == 2
    assert status["single_chain_products"] == 1


def test_compare_finds_a_product_in_both_chains(dataset):
    out = mcp_server.compare("leche semidesnatada")
    assert out["found"] and out["comparable"]
    assert out["offers"]["mercadona"]["price"] == 1.10
    assert out["offers"]["dia"]["price"] == 1.20
    assert out["cheapest"] == "mercadona"


def test_compare_reports_a_single_chain_product_honestly(dataset):
    out = mcp_server.compare("solo en mercadona")
    assert out["found"] and not out["comparable"]
    assert list(out["offers"]) == ["mercadona"]
    assert out["cheapest"] is None


def test_compare_says_not_found(dataset):
    assert mcp_server.compare("pañales talla cuatro")["found"] is False


def test_basket_returns_the_explanation_and_the_plans(dataset):
    out = mcp_server.basket(["2x leche semidesnatada", "aceite de oliva virgen extra"],
                            min_order_mercadona=0.0, free_delivery_dia=None)
    assert out["date"] == "2026-09-05"
    assert "Mejor:" in out["text"]
    # Mercadona is cheaper in products (6,65 vs 7,09) but its delivery costs
    # 3,21 more, so the whole order is cheaper at Dia: 12,08 vs 14,85.
    assert out["best"]["plan"] == "todo en dia"
    assert out["best"]["total"] == round(2 * 1.20 + 4.69 + 4.99, 2)
    assert next(p["plan"] for p in out["plans"]) == "todo en dia"
    assert out["items"][0]["quantity"] == 2
    assert out["not_found"] == []


def test_basket_with_nothing_found_returns_an_error_not_a_plan(dataset):
    out = mcp_server.basket(["cosa que no existe"])
    assert "error" in out and out["not_found"] == ["cosa que no existe"]


def test_price_changes_uses_the_cached_history(dataset):
    out = mcp_server.price_changes(days=7)
    assert (out["since"], out["until"]) == ("2026-09-04", "2026-09-05")
    assert out["tracked"] == 3 and out["changed"] == 1
    assert out["biggest_rises"][0]["name"] == "Leche semidesnatada Hacendado"
    assert out["biggest_rises"][0]["pct"] == 10.0


def test_inflation_refuses_a_short_span(dataset):
    out = mcp_server.inflation()
    assert out["days"] == 1 and out["ine"] is None
    assert "30" in out["note"]


def test_inflation_reaches_the_ine_comparison(dataset, monkeypatch):
    """The `compare` tool once shadowed ine.compare; only a long span exposed it."""
    monkeypatch.setattr(mcp_server, "span_series", lambda days: ("monthly", 7))
    monkeypatch.setattr(mcp_server, "fetch_series",
                        lambda series, last=1: [{"period": "2026-08", "value": 1.6}])
    out = mcp_server.inflation()
    assert out["ine"] == {"series": "monthly", "period": "2026-08", "pct": 1.6}
    assert out["verdict"] in ("por encima del IPC", "por debajo del IPC", "en línea con el IPC")
    assert isinstance(out["gap_pct"], float)


def test_tools_are_registered_with_the_server():
    import asyncio
    tools = asyncio.run(mcp_server.server.list_tools())
    names = {t.name for t in tools}
    assert {"basket", "compare", "price_changes", "inflation", "zone_for_postal_code"} <= names
