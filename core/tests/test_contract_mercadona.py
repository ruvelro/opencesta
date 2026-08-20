"""Live contract tests: is Mercadona's API still shaped like our fixtures?

Run with `pytest -m contract`. These run daily in CI; a failure here means
the adapter needs updating and the assertion message carries the drift.
"""

import pytest

from opencesta.adapters.mercadona import MercadonaAdapter

pytestmark = pytest.mark.contract

PRODUCT_REQUIRED_KEYS = {"id", "display_name", "share_url", "price_instructions"}
PRICE_REQUIRED_KEYS = {"unit_price", "reference_price", "reference_format",
                       "unit_size", "size_format", "tax_percentage", "is_pack",
                       "price_decreased"}


@pytest.fixture(scope="module")
def adapter():
    return MercadonaAdapter()


def test_categories_schema(adapter):
    leaves = adapter.list_leaf_categories("vlc1")
    assert len(leaves) > 50, "category tree shrank suspiciously"


def test_category_detail_schema(adapter):
    detail = adapter.get_category(112, "vlc1")
    products = [p for s in detail.get("categories", []) for p in s.get("products", [])]
    assert products, "leaf category 112 returned no products"
    sample = products[0]
    missing = PRODUCT_REQUIRED_KEYS - sample.keys()
    assert not missing, f"product schema lost keys: {missing}"
    missing_price = PRICE_REQUIRED_KEYS - sample["price_instructions"].keys()
    assert not missing_price, f"price_instructions lost keys: {missing_price}"


def test_product_detail_schema(adapter):
    raw = adapter.get_product("4241", "vlc1")
    for key in ("ean", "brand", "origin", "price_instructions"):
        assert key in raw, f"product detail lost key: {key}"


def test_postal_code_resolution(adapter):
    zone = adapter.zone_for_postal_code("28001")
    assert zone, "change-pc no longer returns x-customer-wh"
