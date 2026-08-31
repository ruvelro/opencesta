"""Unit tests against a golden fixture — no network involved.

The fixture is the verbatim `vike_pageContext` payload of category L2298,
recorded on 2026-08-27.
"""

import json

import httpx
import pytest

from opencesta.adapters.dia import DiaAdapter, page_url, parse_product
from opencesta.models import PriceRecord


def make_adapter(handler) -> DiaAdapter:
    client = httpx.Client(
        base_url="https://www.dia.es", transport=httpx.MockTransport(handler)
    )
    return DiaAdapter(client=client, delay_s=0)


def as_page(payload) -> httpx.Response:
    body = json.dumps(payload, ensure_ascii=False)
    return httpx.Response(
        200,
        text=f'<html><script id="vike_pageContext" type="application/json">{body}</script></html>',
    )


def first_item(fixture):
    return fixture("dia", "category_L2298")["INITIAL_STATE"]["l2"]["plp_items"][0]


def test_parse_product(fixture):
    record = parse_product(first_item(fixture), zone="es-default", captured_at="2026-08-27")
    assert isinstance(record, PriceRecord)
    assert record.chain == "dia"
    assert record.sku == "1807"
    assert record.display_name.startswith("Piña en su jugo")
    assert record.unit_price == 2.19
    assert record.reference_price == 5.25
    assert record.reference_format == "kg"  # normalized from Dia's "KILO"
    assert record.brand == "Dia Fruticampo"
    assert record.url == "https://www.dia.es/conservas-caldos-y-cremas/conservas-de-fruta/p/1807"
    assert record.category == "conservas-caldos-y-cremas"
    assert record.ean is None  # Dia never exposes it


def test_every_fixture_product_parses(fixture):
    groups = fixture("dia", "category_L2298")["INITIAL_STATE"]["l2"]["plp_items"]
    items = [i for i in groups if "sku_id" in i]
    assert items
    for raw in items:
        record = parse_product(raw, zone="es-default", captured_at="2026-08-27")
        assert record.unit_price > 0
        assert record.sku


def test_list_categories_dedupes(fixture):
    payload = fixture("dia", "category_L2298")
    categories = make_adapter(lambda r: as_page(payload)).list_categories()
    assert len(categories) > 20
    links = [c["link"] for c in categories]
    assert len(links) == len(set(links))
    assert all("/c/L" in link for link in links)
    assert all(c["name"] for c in categories)


def test_iter_category_stops_at_last_page(fixture):
    payload = fixture("dia", "category_L2298")
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return as_page(payload)

    records = list(make_adapter(handler).iter_category("/x/c/L1", "es-default", "2026-08-27"))
    # The fixture reports total_pages == 1, so no /pag-2 request may happen.
    assert paths == ["/x/c/L1"]
    assert records


def test_iter_category_paginates_and_respects_robots_cap(fixture):
    payload = json.loads(json.dumps(fixture("dia", "category_L2298")))
    payload["INITIAL_STATE"]["pagination"]["pagination"]["total_pages"] = 99
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return as_page(payload)

    list(make_adapter(handler).iter_category("/x/c/L1", "es-default", "2026-08-27"))
    # robots.txt allows pag-1..5 only; we must stop there even if Dia offers more.
    assert paths == ["/x/c/L1", "/x/pag-2/c/L1", "/x/pag-3/c/L1",
                     "/x/pag-4/c/L1", "/x/pag-5/c/L1"]


def test_iter_products_dedupes_across_categories(fixture):
    payload = fixture("dia", "category_L2298")
    records = list(make_adapter(lambda r: as_page(payload)).iter_products(
        "es-default", "2026-08-27"))
    skus = [r.sku for r in records]
    assert skus and len(skus) == len(set(skus))


def test_empty_category_tree_is_explicit(fixture):
    """The homepage ships the header with an empty list — fail loudly, not silently."""
    payload = json.loads(json.dumps(fixture("dia", "category_L2298")))
    payload["INITIAL_STATE"]["header"]["categoriesData"]["categories"] = []
    adapter = make_adapter(lambda r: as_page(payload))

    with pytest.raises(ValueError, match="no category tree"):
        adapter.list_categories("/")


def test_missing_payload_is_explicit():
    adapter = make_adapter(lambda r: httpx.Response(200, text="<html>nope</html>"))
    with pytest.raises(ValueError, match="no vike_pageContext"):
        adapter.list_categories()


def test_zone_for_postal_code_refuses_to_fake_it():
    adapter = make_adapter(lambda r: httpx.Response(200))
    with pytest.raises(NotImplementedError, match="es-default"):
        adapter.zone_for_postal_code("28001")


def test_page_url_puts_pag_before_the_code():
    # Appending /pag-N to the end 404s on the live site.
    assert page_url("/a/b/c/L2329", 2) == "/a/b/pag-2/c/L2329"
    assert page_url("/no-code-here", 3) == "/no-code-here/pag-3"


def test_dia_snapshot_survives_a_transient_timeout(fixture, monkeypatch):
    monkeypatch.setattr("opencesta.retry.time.sleep", lambda _: None)
    payload = fixture("dia", "category_L2298")
    attempts = []

    def handler(request):
        attempts.append(request.url.path)
        if len(attempts) == 1:
            raise httpx.ConnectError("reset")
        return as_page(payload)

    assert make_adapter(handler).list_categories()
