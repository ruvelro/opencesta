from __future__ import annotations

from dataclasses import dataclass, field, fields

import polars as pl


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


# Declared explicitly rather than inferred per snapshot. A chain that happens to
# leave a column entirely null (Dia never fills `size_format`, Mercadona's
# listing never fills `ean`) would otherwise write it as a Null column, and the
# published dataset could not be read in one pass across chains — which is the
# whole point of publishing it.
SCHEMA: dict[str, pl.DataType] = {
    "chain": pl.String,
    "zone": pl.String,
    "sku": pl.String,
    "display_name": pl.String,
    "category": pl.String,
    "subcategory": pl.String,
    "unit_price": pl.Float64,
    "reference_price": pl.Float64,
    "reference_format": pl.String,
    "unit_size": pl.Float64,
    "size_format": pl.String,
    "tax_pct": pl.Float64,
    "is_pack": pl.Boolean,
    "is_discounted": pl.Boolean,
    "url": pl.String,
    "captured_at": pl.String,
    "brand": pl.String,
    "ean": pl.String,
    "origin": pl.String,
    "thumbnail": pl.String,
}
