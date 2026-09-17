"""A generic adapter for chains that publish Schema.org Product data.

Adding a chain had been a research project each time: find the internal API,
reverse its payload, write bespoke parsing. That does not scale to the dozen
chains a useful comparator needs.

This reads `application/ld+json` instead — the vocabulary sites emit on purpose
so search engines can read their catalogue. It is a published contract rather
than an internal shape we reverse-engineer, which makes it both steadier and
fairer to use. A new chain becomes an entry in `CHAINS`, not a new module.

Discovery goes through the product sitemap the chain declares in robots.txt, so
we only ever ask for pages it has invited crawlers to fetch.
"""

from __future__ import annotations

import gzip
import json
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import httpx

from opencesta import USER_AGENT
from opencesta.match import parse_size
from opencesta.models import PriceRecord
from opencesta.retry import with_retry

_LD_RE = re.compile(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', re.DOTALL)
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")

# Chains whose prices a shopper can actually read on the open web. Keeping the
# whole definition here is the point: a new chain is a few lines, not a module.
#
# `reference_format` is what we express the derived per-unit price in. Sites that
# publish no price-per-unit still state the size in the product name, which
# parse_size reads, so the comparable figure is computed rather than missing —
# without it a chain cannot take part in any cross-chain comparison.


@dataclass(frozen=True, slots=True)
class ChainConfig:
    chain: str
    zone: str
    base_url: str
    product_sitemap: str
    delay_s: float = 0.6


CHAINS: dict[str, ChainConfig] = {
    "ahorramas": ChainConfig(
        chain="ahorramas",
        # Ahorramas is a Madrid-region chain with one published price list.
        zone="madrid",
        base_url="https://www.ahorramas.com",
        product_sitemap="https://www.ahorramas.com/sitemap_0-product.xml",
    ),
}


def iter_json_ld(page: str) -> Iterator[dict[str, Any]]:
    """Every JSON-LD node on the page, flattening lists and @graph wrappers."""
    for block in _LD_RE.findall(page):
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue  # One malformed block must not hide the rest.
        stack = data if isinstance(data, list) else [data]
        while stack:
            node = stack.pop()
            if not isinstance(node, dict):
                continue
            graph = node.get("@graph")
            if isinstance(graph, list):
                stack.extend(graph)
            yield node


def find_product(page: str) -> dict[str, Any] | None:
    for node in iter_json_ld(page):
        if "Product" in str(node.get("@type", "")):
            return node
    return None


def _first_offer(product: dict[str, Any]) -> dict[str, Any]:
    offers = product.get("offers") or {}
    if isinstance(offers, list):
        offers = offers[0] if offers else {}
    return offers if isinstance(offers, dict) else {}


def _text(value: Any) -> str | None:
    """Schema.org lets a field be a string, a node with a name, or a list."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        return _text(value.get("name"))
    if isinstance(value, list):
        for item in value:
            if found := _text(item):
                return found
    return None


def parse_product(
    product: dict[str, Any], url: str, config: ChainConfig, captured_at: str
) -> PriceRecord | None:
    """One JSON-LD Product as a canonical record, or None if it has no price.

    The per-unit price is derived from the size stated in the name, because a
    record without one cannot be compared against another chain at all.
    """
    offer = _first_offer(product)
    price = offer.get("price")
    name = _text(product.get("name"))
    sku = product.get("sku") or product.get("mpn")
    if price in (None, "") or not name or not sku:
        return None
    try:
        unit_price = float(str(price).replace(",", "."))
    except ValueError:
        return None
    if unit_price <= 0:
        return None

    size = parse_size(name)
    reference_price = round(unit_price / size[0], 4) if size and size[0] > 0 else None
    gtin = next(
        (str(product[k]) for k in ("gtin13", "gtin14", "gtin12", "gtin8", "gtin")
         if product.get(k) and str(product[k]).isdigit()),
        None,
    )
    return PriceRecord(
        chain=config.chain,
        zone=config.zone,
        sku=str(sku),
        display_name=name,
        # Schema.org has no category we can trust across chains; the breadcrumb
        # would need a second request per product and buys us little.
        category=_text(product.get("category")) or "",
        subcategory="",
        unit_price=unit_price,
        reference_price=reference_price,
        reference_format=size[1] if size else None,
        unit_size=size[0] if size else None,
        size_format=size[1] if size else None,
        tax_pct=None,
        is_pack=False,
        is_discounted=False,
        url=url,
        captured_at=captured_at,
        brand=_text(product.get("brand")),
        ean=gtin,
        origin=None,
    )


class JsonLdAdapter:
    """Reads a chain's catalogue from the Product JSON-LD on its product pages."""

    def __init__(self, config: ChainConfig, client: httpx.Client | None = None,
                 delay_s: float | None = None):
        self.config = config
        self._client = client or httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
            timeout=30,
            follow_redirects=True,
        )
        self._delay_s = config.delay_s if delay_s is None else delay_s
        self._last_request = 0.0

    @property
    def chain(self) -> str:
        return self.config.chain

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._delay_s:
            time.sleep(self._delay_s - elapsed)
        self._last_request = time.monotonic()

    def _get(self, url: str) -> bytes:
        def once() -> bytes:
            self._throttle()
            resp = self._client.get(url)
            resp.raise_for_status()
            return resp.content

        return with_retry(once)

    def _get_text(self, url: str) -> str:
        raw = self._get(url)
        if raw[:2] == b"\x1f\x8b":  # some sitemaps are served gzipped
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", "replace")

    def known_zones(self) -> list[str]:
        return [self.config.zone]

    def zone_for_postal_code(self, postal_code: str) -> str:
        raise NotImplementedError(
            f"{self.config.chain} publica una sola lista de precios; usa la zona "
            f"'{self.config.zone}'."
        )

    def product_urls(self) -> list[str]:
        """Product URLs from the sitemap the chain publishes for crawlers."""
        urls = _LOC_RE.findall(self._get_text(self.config.product_sitemap))
        if not urls:
            raise ValueError(f"el sitemap {self.config.product_sitemap!r} no lista productos")
        return urls

    def iter_products(self, zone: str, captured_at: str) -> Iterator[PriceRecord]:
        seen: set[str] = set()
        for url in self.product_urls():
            try:
                page = self._get_text(url)
            except httpx.HTTPStatusError:
                continue  # Delisted between the sitemap and our request.
            product = find_product(page)
            if product is None:
                continue
            record = parse_product(product, url, self.config, captured_at)
            if record is None or record.sku in seen:
                continue
            seen.add(record.sku)
            yield record


def make_adapter(chain: str) -> type:
    """A zero-argument adapter class for `chain`, so ADAPTERS stays uniform."""
    config = CHAINS[chain]

    class _Adapter(JsonLdAdapter):
        chain = config.chain  # type: ignore[assignment]

        def __init__(self, client: httpx.Client | None = None, delay_s: float | None = None):
            super().__init__(config, client=client, delay_s=delay_s)

    _Adapter.__name__ = f"{chain.title()}Adapter"
    return _Adapter
