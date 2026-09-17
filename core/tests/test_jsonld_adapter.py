"""Unit tests for the generic Schema.org adapter — no network involved.

The fixture is the verbatim Product JSON-LD Ahorramas published for SKU 57,
recorded on 2026-09-17.
"""

import json

import httpx
import pytest

from opencesta.adapters.jsonld import (
    CHAINS,
    ChainConfig,
    JsonLdAdapter,
    find_product,
    iter_json_ld,
    parse_product,
)
from opencesta.models import PriceRecord

CONFIG = CHAINS["ahorramas"]
URL = "https://www.ahorramas.com/spaghettini-gallo-450g-al-huevo-57.html"


def page(*nodes) -> str:
    blocks = "".join(
        f'<script type="application/ld+json">{json.dumps(n, ensure_ascii=False)}</script>'
        for n in nodes
    )
    return f"<html><head>{blocks}</head><body>x</body></html>"


def make_adapter(handler, config=CONFIG) -> JsonLdAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    return JsonLdAdapter(config, client=client, delay_s=0)


def test_parses_the_real_fixture(fixture):
    product = fixture("ahorramas", "product_57")
    record = parse_product(product, URL, CONFIG, "2026-09-17")

    assert isinstance(record, PriceRecord)
    assert record.chain == "ahorramas"
    assert record.zone == "madrid"
    assert record.sku == "57"
    assert record.display_name == "Spaghettini Gallo 450g al huevo"
    assert record.unit_price == 1.75
    assert record.brand == "GALLO"  # unwrapped from the Brand node
    assert record.url == URL


def test_reference_price_is_derived_from_the_size_in_the_name(fixture):
    """Ahorramas publishes no price-per-unit; without deriving it the product
    could not be compared against any other chain."""
    record = parse_product(fixture("ahorramas", "product_57"), URL, CONFIG, "2026-09-17")
    assert record.unit_size == 0.45
    assert record.reference_format == "kg"
    assert record.reference_price == pytest.approx(1.75 / 0.45, abs=1e-3)


def test_a_name_without_a_size_leaves_the_reference_price_unset():
    product = {"@type": "Product", "name": "Pan de pueblo", "sku": "9",
               "offers": {"price": "1.20"}}
    record = parse_product(product, URL, CONFIG, "2026-09-17")
    assert record.reference_price is None and record.reference_format is None


@pytest.mark.parametrize(
    "product",
    [
        {"@type": "Product", "name": "Sin precio", "sku": "1", "offers": {}},
        {"@type": "Product", "name": "Precio vacío", "sku": "1", "offers": {"price": ""}},
        {"@type": "Product", "name": "Precio no numérico", "sku": "1",
         "offers": {"price": "consultar"}},
        {"@type": "Product", "name": "Precio cero", "sku": "1", "offers": {"price": "0"}},
        {"@type": "Product", "sku": "1", "offers": {"price": "1.0"}},  # sin nombre
        {"@type": "Product", "name": "Sin sku", "offers": {"price": "1.0"}},
    ],
)
def test_unusable_products_are_skipped_not_guessed(product):
    assert parse_product(product, URL, CONFIG, "2026-09-17") is None


def test_offers_may_be_a_list():
    product = {"@type": "Product", "name": "Leche 1 L", "sku": "5",
               "offers": [{"price": "0.99"}, {"price": "9.99"}]}
    assert parse_product(product, URL, CONFIG, "2026-09-17").unit_price == 0.99


def test_gtin_is_taken_only_when_it_is_digits():
    base = {"@type": "Product", "name": "Leche 1 L", "sku": "5", "offers": {"price": "1.0"}}
    assert parse_product({**base, "gtin13": "8412345678901"}, URL, CONFIG, "d").ean == "8412345678901"
    assert parse_product({**base, "gtin13": "n/a"}, URL, CONFIG, "d").ean is None
    assert parse_product(base, URL, CONFIG, "d").ean is None


def test_finds_the_product_inside_a_graph_wrapper():
    """Many sites wrap their nodes in @graph; missing it would find nothing."""
    wrapped = {"@context": "https://schema.org", "@graph": [
        {"@type": "WebPage"}, {"@type": "Product", "name": "Leche 1 L", "sku": "7"}]}
    assert find_product(page(wrapped))["sku"] == "7"


def test_a_malformed_block_does_not_hide_the_rest():
    html = ('<script type="application/ld+json">{no es json}</script>'
            + page({"@type": "Product", "name": "Leche 1 L", "sku": "7"}))
    assert find_product(html)["sku"] == "7"


def test_no_product_on_the_page_is_none():
    assert find_product(page({"@type": "BreadcrumbList"})) is None
    assert len(list(iter_json_ld(page({"@type": "BreadcrumbList"})))) == 1


def test_product_urls_come_from_the_sitemap():
    def handler(request):
        assert str(request.url) == CONFIG.product_sitemap
        return httpx.Response(200, text=
            '<urlset><url><loc>https://x.test/a.html</loc></url>'
            '<url><loc>https://x.test/b.html</loc></url></urlset>')

    assert make_adapter(handler).product_urls() == ["https://x.test/a.html", "https://x.test/b.html"]


def test_a_gzipped_sitemap_is_decompressed():
    import gzip
    body = gzip.compress(b"<urlset><url><loc>https://x.test/a.html</loc></url></urlset>")
    adapter = make_adapter(lambda r: httpx.Response(200, content=body))
    assert adapter.product_urls() == ["https://x.test/a.html"]


def test_an_empty_sitemap_is_an_error_not_an_empty_snapshot():
    adapter = make_adapter(lambda r: httpx.Response(200, text="<urlset></urlset>"))
    with pytest.raises(ValueError, match="no lista productos"):
        adapter.product_urls()


def test_iter_products_skips_delisted_and_dedupes():
    product = {"@type": "Product", "name": "Leche 1 L", "sku": "7", "offers": {"price": "1.0"}}

    def handler(request):
        url = str(request.url)
        if url == CONFIG.product_sitemap:
            return httpx.Response(200, text="".join(
                f"<url><loc>https://x.test/{n}.html</loc></url>" for n in ("a", "b", "gone")))
        if url.endswith("gone.html"):
            return httpx.Response(404)
        return httpx.Response(200, text=page(product))

    records = list(make_adapter(handler).iter_products("madrid", "2026-09-17"))
    assert [r.sku for r in records] == ["7"]  # same SKU twice, kept once


def test_zone_for_postal_code_refuses_rather_than_faking_one():
    adapter = make_adapter(lambda r: httpx.Response(200))
    with pytest.raises(NotImplementedError, match="madrid"):
        adapter.zone_for_postal_code("28001")


def test_the_default_client_identifies_itself():
    """Every request must say who we are; DATA_POLICY point 4 depends on it."""
    adapter = JsonLdAdapter(CONFIG)
    assert adapter._client.headers["User-Agent"].startswith("OpenCesta/")
    assert adapter._client.headers["Accept-Encoding"] == "gzip"


def test_every_configured_chain_is_usable():
    for name, config in CHAINS.items():
        assert isinstance(config, ChainConfig)
        assert config.chain == name
        assert config.product_sitemap.startswith(config.base_url)
        assert config.delay_s >= 0.4  # politeness floor
