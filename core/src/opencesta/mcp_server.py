"""Phase 6: ask OpenCesta from Claude.

An MCP server over the published dataset. It fetches the latest releases into
a local cache on first use, so a `claude mcp add` is all anyone needs — no
clone, no snapshot, no API key. Every tool answers in euros with a reason,
because the point of the whole project is that "cheaper" is explainable.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from typing import Any

from mcp.server.mcpserver import MCPServer

from opencesta.basket import (
    DEFAULT_TERMS,
    Terms,
    build_offers,
    explain,
    find_item,
    optimize,
    parse_list,
)
from opencesta.data import ensure_data
from opencesta.history import available_dates, diff
from opencesta.ine import compare as ine_compare
from opencesta.ine import fetch_series, span_series
from opencesta.match import load_for_matching

CHAIN_A, ZONE_A = "mercadona", os.environ.get("OPENCESTA_ZONE", "vlc1")
CHAIN_B, ZONE_B = "dia", "es-default"

server = MCPServer(
    "opencesta",
    instructions=(
        "Precios de supermercados españoles (Mercadona y Dia) con histórico diario. "
        "Usa `basket` para saber dónde comprar una lista, `compare` para un producto "
        "suelto, `price_changes` para ver qué ha subido o bajado, e `inflation` para "
        "contrastar la cesta con el IPC del INE. Las respuestas explican el porqué en euros."
    ),
)


@lru_cache(maxsize=1)
def _dataset(days: int = 2) -> dict[str, Any]:
    root = ensure_data(days=days)
    prices = root / "data"
    (records_a, records_b), date = load_for_matching(prices, (CHAIN_A, ZONE_A), (CHAIN_B, ZONE_B))
    eq_path = root / "equivalences.jsonl"
    equivalences = []
    if eq_path.exists():
        equivalences = [
            json.loads(line)
            for line in eq_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
    pairs, singles = build_offers(records_a, records_b, equivalences, CHAIN_A, CHAIN_B)
    return {"root": root, "prices": prices, "date": date, "pairs": pairs, "singles": singles}


def _terms(delivery_a, min_a, free_a, delivery_b, min_b, free_b) -> dict[str, Terms]:
    return {
        CHAIN_A: Terms(CHAIN_A, delivery_a, min_a, free_a),
        CHAIN_B: Terms(CHAIN_B, delivery_b, min_b, free_b),
    }


@server.tool()
def basket(
    items: list[str],
    delivery_mercadona: float = DEFAULT_TERMS["mercadona"][0],
    min_order_mercadona: float = DEFAULT_TERMS["mercadona"][1],
    free_delivery_mercadona: float | None = DEFAULT_TERMS["mercadona"][2],
    delivery_dia: float = DEFAULT_TERMS["dia"][0],
    min_order_dia: float = DEFAULT_TERMS["dia"][1],
    free_delivery_dia: float | None = DEFAULT_TERMS["dia"][2],
    split_penalty: float = 0.0,
) -> dict[str, Any]:
    """Dónde comprar una lista de la compra y por qué.

    `items`: una línea por producto, en lenguaje natural ("2x leche semidesnatada 6 L",
    "aceite de oliva virgen extra", "pañales talla 4"). Devuelve el mejor plan y todos
    los planes costeados con envío y pedido mínimo, más el detalle por producto.
    Las condiciones de envío cambian por código postal: pásalas si conoces las tuyas.
    """
    data = _dataset()
    found, not_found = [], []
    for query, quantity in parse_list("\n".join(items)):
        item = find_item(query, quantity, data["pairs"], data["singles"])
        (found if item else not_found).append(item or (query, quantity))
    if not found:
        return {"date": data["date"], "error": "ningún producto de la lista se ha encontrado",
                "not_found": [q for q, _ in not_found]}
    terms = _terms(delivery_mercadona, min_order_mercadona, free_delivery_mercadona,
                   delivery_dia, min_order_dia, free_delivery_dia)
    plans = optimize(found, terms, split_penalty)
    best = next((p for p in plans if p.feasible), None)
    return {
        "date": data["date"],
        "text": explain(found, plans, terms, not_found),
        "best": None if best is None else {"plan": best.label, "total": best.total},
        "plans": [
            {"plan": p.label, "products": p.products, "delivery": p.delivery,
             "total": p.total, "feasible": p.feasible, "reason": p.reason}
            for p in plans
        ],
        "items": [
            {"query": i.query, "quantity": i.quantity,
             "offers": {c: {"name": o.name, "price": o.unit_price,
                            "reference_price": o.reference_price,
                            "reference_format": o.reference_format}
                        for c, o in i.offers.items()}}
            for i in found
        ],
        "not_found": [q for q, _ in not_found],
    }


@server.tool()
def compare(query: str) -> dict[str, Any]:
    """Cuánto cuesta un producto en cada cadena.

    Busca el producto por nombre ("tónica zero schweppes", "arroz basmati 1 kg") y
    devuelve la oferta de cada cadena con su precio de referencia (€/kg, €/L, €/ud),
    que es lo comparable. Si solo existe en una cadena, lo dice.
    """
    data = _dataset()
    item = find_item(query, 1, data["pairs"], data["singles"])
    if item is None:
        return {"date": data["date"], "query": query, "found": False}
    offers = {c: {"name": o.name, "price": o.unit_price, "reference_price": o.reference_price,
                  "reference_format": o.reference_format} for c, o in item.offers.items()}
    cheapest = item.cheapest_chain if len(item.offers) > 1 else None
    return {"date": data["date"], "query": query, "found": True, "comparable": len(offers) > 1,
            "how_matched": item.method, "offers": offers, "cheapest": cheapest}


@server.tool()
def price_changes(
    chain: str = CHAIN_A, zone: str = ZONE_A, days: int = 7, top: int = 10
) -> dict[str, Any]:
    """Qué ha subido y bajado de precio en una cadena y zona en los últimos `days` días.

    Compara el snapshot más antiguo disponible en ese tramo con el más reciente:
    mayores subidas y bajadas, productos nuevos y descatalogados, y la variación del
    catálogo comparable.
    """
    root = ensure_data(days=days + 1)
    dates = available_dates(root / "data", chain, zone)
    if len(dates) < 2:
        return {"error": f"hace falta más de un snapshot de {chain}/{zone}", "dates": dates}
    result = diff(root / "data", chain, zone)
    changed = result["changed"]
    rows = lambda frame: [
        {"name": r["display_name"], "from": r[result["since"]], "to": r[result["until"]],
         "pct": r["pct"]} for r in frame.iter_rows(named=True)
    ]
    return {
        "chain": chain, "zone": zone, "since": result["since"], "until": result["until"],
        "tracked": result["tracked"], "changed": changed.height,
        "added": result["added"].height, "removed": result["removed"].height,
        "basket_pct": result["basket_pct"],
        "biggest_drops": rows(changed.head(top)),
        "biggest_rises": rows(changed.tail(top).reverse()),
    }


@server.tool()
def inflation(chain: str = CHAIN_A, zone: str = ZONE_A) -> dict[str, Any]:
    """La variación real de la cesta frente al IPC de alimentación del INE.

    Solo compara cuando el tramo de histórico equivale a una serie oficial (~30 días
    contra la variación mensual, ~365 contra la anual). Con menos histórico lo dice y no
    compara: enfrentar una semana de cesta a una cifra anual sería engañoso.
    """
    root = ensure_data(days=40)
    dates = available_dates(root / "data", chain, zone)
    if len(dates) < 2:
        return {"error": "hace falta más de un snapshot", "dates": dates}
    result = diff(root / "data", chain, zone)
    import datetime as dt
    span = (dt.date.fromisoformat(result["until"]) - dt.date.fromisoformat(result["since"])).days
    out: dict[str, Any] = {"chain": chain, "zone": zone, "since": result["since"],
                           "until": result["until"], "days": span,
                           "basket_pct": result["basket_pct"]}
    choice = span_series(span)
    if choice is None:
        out["ine"] = None
        out["note"] = (f"{span} días no se corresponden con ninguna serie del INE; hacen falta "
                       "~30 para la mensual o ~365 para la anual")
        return out
    series, _ = choice
    official = fetch_series(series, last=1)
    if not official:
        out["ine"] = None
        out["note"] = "el INE no devolvió datos"
        return out
    verdict = ine_compare(result["basket_pct"], official[-1]["value"])
    out["ine"] = {"series": series, "period": official[-1]["period"], "pct": official[-1]["value"]}
    out["gap_pct"] = verdict["gap_pct"]
    out["verdict"] = verdict["verdict"]
    return out


@server.tool()
def zone_for_postal_code(postal_code: str) -> dict[str, str]:
    """La zona de precios de Mercadona para un código postal (ej. 28001 → mad3)."""
    from opencesta.adapters.mercadona import MercadonaAdapter

    return {"postal_code": postal_code, "zone": MercadonaAdapter().zone_for_postal_code(postal_code)}


@server.resource("opencesta://status")
def status() -> str:
    """Qué datos tiene cargados el servidor: cadenas, zonas, fecha y nº de equivalencias."""
    data = _dataset()
    return json.dumps({
        "chains": {CHAIN_A: ZONE_A, CHAIN_B: ZONE_B},
        "date": data["date"],
        "comparable_pairs": len(data["pairs"]),
        "single_chain_products": len(data["singles"]),
        "cache": str(data["root"]),
    }, ensure_ascii=False)


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
