from opencesta.adapters.base import Adapter
from opencesta.adapters.dia import DiaAdapter
from opencesta.adapters.jsonld import CHAINS as JSONLD_CHAINS
from opencesta.adapters.jsonld import make_adapter
from opencesta.adapters.mercadona import MercadonaAdapter

ADAPTERS: dict[str, type[Adapter]] = {
    MercadonaAdapter.chain: MercadonaAdapter,
    DiaAdapter.chain: DiaAdapter,
    # Chains read through their published Schema.org data: adding one is a
    # config entry in jsonld.CHAINS, not another module.
    **{name: make_adapter(name) for name in JSONLD_CHAINS},
}

__all__ = ["ADAPTERS", "Adapter", "DiaAdapter", "MercadonaAdapter", "make_adapter"]
