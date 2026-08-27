from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator
from typing import Any

import httpx

from opencesta import USER_AGENT
from opencesta.models import PriceRecord
from opencesta.transport import UrllibTransport

BASE_URL = "https://www.dia.es"

# Dia server-renders its pages and embeds the whole view model in one JSON
# script tag, so there is no separate listing API to call: fetch the category
# page, read this payload. Product pages expose the same shape.
PAGE_CONTEXT_RE = re.compile(
    r'<script id="vike_pageContext" type="application/json">(.*?)</script>', re.DOTALL
)

# robots.txt allows */pag-1..5 and disallows the rest, so we never page past 5.
MAX_PAGE = 5

# Anonymous requests get Dia's default national catalog. Real per-postal-code
# pricing needs a session we deliberately do not create (see DATA_POLICY), so
# this is labelled honestly rather than passed off as a resolved zone.
DEFAULT_ZONE = "es-default"

# Exactly what we send, and nothing else. Akamai 403s the classic Python client
# signature "gzip, deflate"; a browser sends "gzip, deflate, br". We send plain
# "gzip", which is none of those: it is literally true (we do decode gzip), it
# imitates no browser, and it costs Dia ~6x less bandwidth than sending no
# accept-encoding at all — which DATA_POLICY point 3 requires of us.
MINIMAL_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Encoding": "gzip",
}

# Any category page carries the full tree in its header; the homepage ships the
# same object with an empty list, so discovery has to start from one of these.
SEED_CATEGORY = "/conservas-caldos-y-cremas/conservas-de-fruta/c/L2298"


class DiaAdapter:
    """Reads Dia's server-rendered category pages via their embedded JSON payload.

    Unlike Mercadona there is no public JSON listing endpoint: `/api/v1/pdp-back/<sku>`
    serves one product at a time, which would mean ~6800 requests per snapshot.
    Category pages carry 20 products each in `INITIAL_STATE.l2.plp_items`, so a
    full catalog costs a few hundred polite requests instead.
    """

    chain = "dia"

    def __init__(self, client: httpx.Client | None = None, delay_s: float = 0.6):
        self._client = client or self.build_client()
        self._delay_s = delay_s
        self._last_request = 0.0

    @staticmethod
    def build_client() -> httpx.Client:
        client = httpx.Client(
            base_url=BASE_URL,
            headers=MINIMAL_HEADERS,
            timeout=30,
            follow_redirects=True,
            transport=UrllibTransport(),
        )
        # httpx installs its own defaults; drop everything we did not choose so
        # the request stays exactly as declared in MINIMAL_HEADERS.
        for header in list(client.headers):
            if header.lower() not in {h.lower() for h in MINIMAL_HEADERS}:
                del client.headers[header]
        return client
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._delay_s:
            time.sleep(self._delay_s - elapsed)
        self._last_request = time.monotonic()

    def _get_page_context(self, path: str) -> dict[str, Any]:
        self._throttle()
        resp = self._client.get(path)
        resp.raise_for_status()
        match = PAGE_CONTEXT_RE.search(resp.text)
        if not match:
            raise ValueError(f"no vike_pageContext payload at {path!r}")
        return json.loads(match.group(1))

    def known_zones(self) -> list[str]:
        return [DEFAULT_ZONE]

    def zone_for_postal_code(self, postal_code: str) -> str:
        # Kept explicit rather than silently returning the default: pretending a
        # postal code was honoured would poison the (sku, zone, date) key.
        raise NotImplementedError(
            "Dia serves a single anonymous catalog; per-postal-code pricing is not "
            "resolved yet. Use zone 'es-default'."
        )

    def list_categories(self, path: str = SEED_CATEGORY) -> list[dict[str, str]]:
        """Leaf categories from the tree in the site header.

        Seeded from a category page, not `/`: the homepage ships the same header
        object with an empty `categories` list, so entering there yields nothing.

        Only leaves are walked: a parent listing repeats its children's products,
        so crawling both would double the requests for nothing.
        """
        context = self._get_page_context(path)
        tree = context["INITIAL_STATE"]["header"]["categoriesData"]["categories"]
        if not tree:
            raise ValueError(f"no category tree at {path!r}; seed from a category page")
        seen: dict[str, str] = {}
        for top in tree:
            leaves = top.get("children") or [top]
            for leaf in leaves:
                seen.setdefault(leaf["link"], leaf["name"])
        return [{"link": link, "name": name} for link, name in seen.items()]

    def iter_category(self, link: str, zone: str, captured_at: str) -> Iterator[PriceRecord]:
        page = 1
        while page <= MAX_PAGE:
            path = link if page == 1 else page_url(link, page)
            state = self._get_page_context(path)["INITIAL_STATE"]
            items = [raw for raw in state.get("l2", {}).get("plp_items") or [] if is_product(raw)]
            for raw in items:
                yield parse_product(raw, zone=zone, captured_at=captured_at)
            total_pages = (state.get("pagination") or {}).get("pagination", {}).get("total_pages", 1)
            if not items or page >= min(total_pages, MAX_PAGE):
                return
            page += 1

    def iter_products(self, zone: str, captured_at: str) -> Iterator[PriceRecord]:
        seen: set[str] = set()
        for category in self.list_categories():
            for record in self.iter_category(category["link"], zone, captured_at):
                if record.sku in seen:
                    continue
                seen.add(record.sku)
                yield record


def page_url(link: str, page: int) -> str:
    """Insert `/pag-N` before the `/c/<code>` segment, which is where Dia wants it.

    Appending it to the end 404s. This is also the form robots.txt governs
    (`Allow: */pag-1..5`), so keeping it means the cap stays meaningful.
    """
    head, sep, tail = link.rpartition("/c/")
    if not sep:
        return f"{link}/pag-{page}"
    return f"{head}/pag-{page}/c/{tail}"


def is_product(raw: dict[str, Any]) -> bool:
    """`plp_items` interleaves layout markers (e.g. PaginationDivider) with products."""
    return "sku_id" in raw and "prices" in raw


def parse_product(raw: dict[str, Any], *, zone: str, captured_at: str) -> PriceRecord:
    prices = raw["prices"]
    url = raw.get("url", "")
    parts = [p for p in url.split("/") if p]
    return PriceRecord(
        chain=DiaAdapter.chain,
        zone=zone,
        sku=str(raw["sku_id"]),
        display_name=raw["display_name"],
        category=parts[0] if parts else "",
        subcategory=parts[1] if len(parts) > 1 else "",
        unit_price=float(prices["price"]),
        reference_price=_opt_float(prices.get("price_per_unit")),
        reference_format=_REFERENCE_FORMATS.get(prices.get("measure_unit")),
        unit_size=None,  # Dia states net content only as free text in the title.
        size_format=None,
        tax_pct=None,
        is_pack=False,
        is_discounted=bool(prices.get("discount_percentage")),
        url=f"{BASE_URL}{url}" if url.startswith("/") else url,
        captured_at=captured_at,
        brand=raw.get("brand"),
        ean=None,  # Not exposed anywhere in Dia's public payloads.
        origin=None,
    )


# Mercadona reports "L"/"kg"; normalize Dia's wording onto the same vocabulary
# so reference prices stay comparable across chains.
_REFERENCE_FORMATS = {
    "LITRO": "L",
    "KILO": "kg",
    "UNIDAD": "ud",
    "METRO": "m",
    "LAVADO": "lavado",
}


def _opt_float(value: Any) -> float | None:
    return None if value in (None, "") else float(value)
