"""Live contract tests: is Dia's page payload still shaped like our fixture?

Run with `pytest -m contract`. These run daily in CI; a failure means the
adapter needs updating and the assertion message carries the drift.
"""

import pytest

from opencesta.adapters.dia import SEED_CATEGORY, DiaAdapter, is_product

pytestmark = pytest.mark.contract

PRODUCT_REQUIRED_KEYS = {"sku_id", "display_name", "prices", "url"}
PRICE_REQUIRED_KEYS = {"price", "price_per_unit", "measure_unit", "currency"}


@pytest.fixture(scope="module")
def adapter():
    return DiaAdapter()


def test_minimal_headers_still_accepted(adapter):
    """Guards the DATA_POLICY position: our declared headers must still get through.

    If this starts failing with 403, Dia is refusing our identified agent and the
    answer is to stop crawling them — not to make the request look more browsery.
    """
    context = adapter._get_page_context(SEED_CATEGORY)
    assert context["INITIAL_STATE"]


def test_category_tree_present(adapter):
    categories = adapter.list_categories()
    assert len(categories) > 100, "category tree shrank suspiciously"
    assert all("/c/L" in c["link"] for c in categories)


def test_category_payload_schema(adapter):
    state = adapter._get_page_context(SEED_CATEGORY)["INITIAL_STATE"]
    items = [raw for raw in state["l2"]["plp_items"] if is_product(raw)]
    assert items, "seed category returned no products"

    missing = PRODUCT_REQUIRED_KEYS - items[0].keys()
    assert not missing, f"product schema lost keys: {missing}"
    missing_price = PRICE_REQUIRED_KEYS - items[0]["prices"].keys()
    assert not missing_price, f"prices lost keys: {missing_price}"
    assert "pagination" in state["pagination"], "pagination shape changed"


def test_pagination_url_form_still_valid(adapter):
    """`/pag-N` belongs before `/c/<code>`; appending it 404s."""
    categories = adapter.list_categories()
    multipage = None
    for category in categories:
        state = adapter._get_page_context(category["link"])["INITIAL_STATE"]
        if state["pagination"]["pagination"]["total_pages"] > 1:
            multipage = category["link"]
            break
    if multipage is None:
        pytest.skip("no multi-page category found to exercise pagination")

    records = list(adapter.iter_category(multipage, "es-default", "2026-01-01"))
    assert len(records) > 20, "pagination stopped after the first page"
