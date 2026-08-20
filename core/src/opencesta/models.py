from __future__ import annotations

from dataclasses import dataclass, field, fields


@dataclass(frozen=True, slots=True)
class PriceRecord:
    """Canonical price observation: (sku, chain, zone, date) -> price.

    Prices are euros as Decimal-safe strings converted to float at the edge;
    `unit_price` is the shelf price of the selling unit, `reference_price`
    is the normalized price per `reference_format` (€/kg, €/L, €/ud).
    """

    chain: str
    zone: str
    sku: str
    display_name: str
    category: str
    subcategory: str
    unit_price: float
    reference_price: float | None
    reference_format: str | None
    unit_size: float | None
    size_format: str | None
    tax_pct: float | None
    is_pack: bool
    is_discounted: bool
    url: str
    captured_at: str  # ISO date, e.g. "2026-08-20"
    brand: str | None = None
    ean: str | None = None
    origin: str | None = None
    thumbnail: str | None = field(default=None, repr=False)

    @classmethod
    def columns(cls) -> list[str]:
        return [f.name for f in fields(cls)]
