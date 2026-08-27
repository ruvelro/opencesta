from opencesta.adapters.base import Adapter
from opencesta.adapters.dia import DiaAdapter
from opencesta.adapters.mercadona import MercadonaAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    MercadonaAdapter.chain: MercadonaAdapter,
    DiaAdapter.chain: DiaAdapter,
}

__all__ = ["ADAPTERS", "Adapter", "DiaAdapter", "MercadonaAdapter"]
