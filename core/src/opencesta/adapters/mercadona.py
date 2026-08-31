from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

import httpx

from opencesta import USER_AGENT
from opencesta.models import PriceRecord
from opencesta.retry import with_retry

BASE_URL = "https://tienda.mercadona.es/api"

# Warehouses seen in the wild; the full map is discovered via zone_for_postal_code.
KNOWN_ZONES = ["vlc1", "mad1", "mad2", "mad3", "bcn1", "alc1", "svq1"]


class MercadonaAdapter:
    """Reads Mercadona's internal JSON API (the one its own PWA consumes).

    Endpoints:
      GET /categories/?lang=es&wh=<zone>          -> top-level category tree
      GET /categories/<id>/?lang=es&wh=<zone>     -> leaf category with embedded products
      GET /products/<id>/?lang=es&wh=<zone>       -> product detail (ean, brand, origin)
      PUT /postal-codes/actions/change-pc/        -> x-customer-wh header maps CP -> zone
    """

    chain = "mercadona"

    def __init__(self, client: httpx.Client | None = None, delay_s: float = 0.4):
        self._client = client or httpx.Client(
            base_url=BASE_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=30,
        )
        self._delay_s = delay_s
        self._last_request = 0.0

    def _get(self, path: str, zone: str) -> dict[str, Any]:
        def once() -> dict[str, Any]:
            self._throttle()
            resp = self._client.get(path, params={"lang": "es", "wh": zone})
            resp.raise_for_status()
            return resp.json()

        return with_retry(once)

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._delay_s:
            time.sleep(self._delay_s - elapsed)
        self._last_request = time.monotonic()

    def known_zones(self) -> list[str]:
        return list(KNOWN_ZONES)

    def zone_for_postal_code(self, postal_code: str) -> str:
        self._throttle()
        resp = self._client.put(
            "/postal-codes/actions/change-pc/",
            json={"new_postal_code": postal_code},
        )
        resp.raise_for_status()
        zone = resp.headers.get("x-customer-wh")
        if not zone:
            raise ValueError(f"no x-customer-wh header for postal code {postal_code!r}")
        return zone

    def list_leaf_categories(self, zone: str) -> list[dict[str, Any]]:
        """Flatten the category tree into leaf categories, annotated with their parent."""
        tree = self._get("/categories/", zone)
        leaves = []
        for top in tree["results"]:
            for sub in top.get("categories", []):
                leaves.append({"id": sub["id"], "name": sub["name"], "parent": top["name"]})
        return leaves

    def get_category(self, category_id: int, zone: str) -> dict[str, Any]:
        return self._get(f"/categories/{category_id}/", zone)

    def get_product(self, product_id: str, zone: str) -> dict[str, Any]:
        return self._get(f"/products/{product_id}/", zone)

    def iter_products(self, zone: str, captured_at: str) -> Iterator[PriceRecord]:
        """Walk every leaf category; products come embedded, so no per-product calls.

        EAN/brand/origin live only in the product detail endpoint and are left
        null here — enriching a subset of SKUs is a separate, slower pass.
        """
        seen: set[str] = set()
        for leaf in self.list_leaf_categories(zone):
            detail = self.get_category(leaf["id"], zone)
            for section in detail.get("categories", []):
                for raw in section.get("products", []):
                    if raw["id"] in seen:
                        continue
                    seen.add(raw["id"])
                    yield parse_product(raw, zone=zone, category=leaf["parent"],
                                        subcategory=leaf["name"], captured_at=captured_at)


def parse_product(
    raw: dict[str, Any], *, zone: str, category: str, subcategory: str, captured_at: str
) -> PriceRecord:
    price = raw["price_instructions"]
    return PriceRecord(
        chain=MercadonaAdapter.chain,
        zone=zone,
        sku=str(raw["id"]),
        display_name=raw["display_name"],
        category=category,
        subcategory=subcategory,
        unit_price=float(price["unit_price"]),
        reference_price=_opt_float(price.get("reference_price")),
        reference_format=price.get("reference_format"),
        unit_size=_opt_float(price.get("unit_size")),
        size_format=price.get("size_format"),
        tax_pct=_opt_float(price.get("tax_percentage")),
        is_pack=bool(price.get("is_pack")),
        is_discounted=bool(price.get("price_decreased")),
        url=raw.get("share_url", ""),
        captured_at=captured_at,
        brand=raw.get("brand"),
        ean=str(raw["ean"]) if raw.get("ean") else None,
        origin=raw.get("origin"),
        thumbnail=raw.get("thumbnail"),
    )


def _opt_float(value: Any) -> float | None:
    return None if value in (None, "") else float(value)
