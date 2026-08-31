"""Unit tests against golden fixtures — no network involved.

The fixtures are verbatim API responses recorded on 2026-08-20 (zone vlc1).
If the live schema drifts, the daily contract test catches it; these tests
pin down what our parser must keep understanding.
"""

import httpx
import pytest

from opencesta.adapters.mercadona import MercadonaAdapter, parse_product
from opencesta.models import PriceRecord


def make_adapter(handler) -> MercadonaAdapter:
    client = httpx.Client(
        base_url="https://tienda.mercadona.es/api",
        transport=httpx.MockTransport(handler),
    )
    return MercadonaAdapter(client=client, delay_s=0)


def test_list_leaf_categories(fixture):
    def handler(request):
        assert request.url.params["wh"] == "vlc1"
        return httpx.Response(200, json=fixture("mercadona", "categories"))

    leaves = make_adapter(handler).list_leaf_categories("vlc1")
    assert len(leaves) > 50
    first = leaves[0]
    assert first == {"id": 112, "name": "Aceite, vinagre y sal", "parent": "Aceite, especias y salsas"}


def test_parse_category_products(fixture):
    detail = fixture("mercadona", "category_112")
    products = [p for section in detail["categories"] for p in section["products"]]
    assert len(products) > 10

    record = parse_product(
        products[0], zone="vlc1", category="Aceite, especias y salsas",
        subcategory="Aceite, vinagre y sal", captured_at="2026-08-20",
    )
    assert isinstance(record, PriceRecord)
    assert record.chain == "mercadona"
    assert record.zone == "vlc1"
    assert record.sku == "4241"
    assert record.display_name == "Aceite de oliva 0,4º Hacendado"
    assert record.unit_price > 0
    assert record.reference_price is not None
    assert record.reference_format == "L"
    assert record.url.startswith("https://tienda.mercadona.es/product/")
    # Category listings don't carry EAN/brand — only the detail endpoint does.
    assert record.ean is None


def test_every_fixture_product_parses(fixture):
    detail = fixture("mercadona", "category_112")
    for section in detail["categories"]:
        for raw in section["products"]:
            record = parse_product(
                raw, zone="vlc1", category="c", subcategory="s", captured_at="2026-08-20"
            )
            assert record.unit_price > 0
            assert record.sku


def test_parse_product_detail(fixture):
    raw = fixture("mercadona", "product_4241")
    record = parse_product(
        raw, zone="vlc1", category="c", subcategory="s", captured_at="2026-08-20"
    )
    assert record.ean == "8402001027482"
    assert record.brand == "Hacendado"
    assert record.origin == "España"


def test_zone_for_postal_code():
    def handler(request):
        assert request.method == "PUT"
        assert request.url.path.endswith("/postal-codes/actions/change-pc/")
        return httpx.Response(
            200, json={"warehouse_changed": False}, headers={"x-customer-wh": "mad3"}
        )

    assert make_adapter(handler).zone_for_postal_code("28001") == "mad3"


def test_zone_for_postal_code_missing_header():
    def handler(request):
        return httpx.Response(200, json={})

    with pytest.raises(ValueError):
        make_adapter(handler).zone_for_postal_code("00000")


def test_iter_products_dedupes_and_walks_all_leaves(fixture):
    categories = fixture("mercadona", "categories")
    detail = fixture("mercadona", "category_112")
    calls = []

    def handler(request):
        if request.url.path == "/api/categories/":
            return httpx.Response(200, json=categories)
        calls.append(request.url.path)
        return httpx.Response(200, json=detail)

    records = list(make_adapter(handler).iter_products("vlc1", "2026-08-20"))
    n_leaves = sum(len(t["categories"]) for t in categories["results"])
    assert len(calls) == n_leaves
    # Same fixture served for every leaf -> dedupe keeps each SKU once.
    skus = [r.sku for r in records]
    assert len(skus) == len(set(skus))
    assert len(skus) > 10


def test_snapshot_survives_a_transient_timeout(fixture, monkeypatch):
    """The 2026-08-31 failure: one ReadTimeout mid-walk killed the whole snapshot."""
    monkeypatch.setattr("opencesta.retry.time.sleep", lambda _: None)
    attempts = []

    def handler(request):
        attempts.append(request.url.path)
        if len(attempts) == 2:  # blip on the second call, mid-walk
            raise httpx.ReadTimeout("boom")
        return httpx.Response(200, json=fixture("mercadona", "categories"))

    leaves = make_adapter(handler).list_leaf_categories("alc1")
    assert leaves  # the retry absorbed it


def test_a_404_still_surfaces_immediately(monkeypatch):
    monkeypatch.setattr("opencesta.retry.time.sleep", lambda _: None)
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(404)

    with pytest.raises(httpx.HTTPStatusError):
        make_adapter(handler).get_product("nope", "vlc1")
    assert len(calls) == 1
