from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from opencesta.models import PriceRecord


@runtime_checkable
class Adapter(Protocol):
    """Common interface every chain adapter implements.

    Adapters talk to the chain's own JSON API (the one its public web/PWA
    consumes), never scrape HTML, and always rate-limit themselves. A price
    only exists relative to a zone: adapters must never expose a zoneless
    price.
    """

    chain: str

    def known_zones(self) -> list[str]: ...

    def zone_for_postal_code(self, postal_code: str) -> str: ...

    def iter_products(self, zone: str, captured_at: str) -> Iterator[PriceRecord]: ...
