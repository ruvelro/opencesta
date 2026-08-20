from opencesta.adapters.base import Adapter
from opencesta.adapters.mercadona import MercadonaAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    MercadonaAdapter.chain: MercadonaAdapter,
}

__all__ = ["ADAPTERS", "Adapter", "MercadonaAdapter"]
