"""Phase 5: where to buy a shopping list, and why.

Everything before this produced data; this turns it into an answer. Give it a
list, and it prices the list at each chain, prices the split that buys every
item wherever it is cheapest, and then says which of those actually wins once
delivery, minimum order and the nuisance of two deliveries are counted.

The answer is deliberately explainable rather than clever. With two chains the
whole decision space is three plans — all at A, all at B, or split — and each
can be laid out in euros with a reason. A shopper trusts "the split saves 1,90
in products but costs 7,21 more in delivery, so no" far more than a number that
fell out of a solver.

Only products the matcher has paired across chains (see match.py) are treated
as comparable. A product found in one chain alone is bought there or not at
all; it never gets silently swapped for something the matcher never judged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from opencesta.match import (
    parse_size,
    record_size,
    score_pair,
    sizes_agree,
    standalone_numbers,
    tokenize,
)

# "2x leche", "2 x leche", "leche x2", "leche"
_QTY_PREFIX = re.compile(r"^\s*(\d+)\s*[x×]\s*(.+)$", re.IGNORECASE)
_QTY_SUFFIX = re.compile(r"^(.+?)\s*[x×]\s*(\d+)\s*$", re.IGNORECASE)

# The least of a query's words that must appear in a product for it to count as
# found. Below this we say "not found" rather than hand back something else.
MIN_COVERAGE = 0.5



@dataclass(frozen=True, slots=True)
class Offer:
    chain: str
    sku: str
    name: str
    unit_price: float
    reference_price: float | None
    reference_format: str | None


@dataclass(slots=True)
class Item:
    query: str
    quantity: int
    offers: dict[str, Offer]
    method: str  # how the cross-chain link is known; "single" when there is none
    score: float

    def cost(self, chain: str) -> float | None:
        offer = self.offers.get(chain)
        return None if offer is None else round(offer.unit_price * self.quantity, 2)

    @property
    def cheapest_chain(self) -> str:
        return min(self.offers, key=lambda c: self.offers[c].unit_price)


@dataclass(frozen=True, slots=True)
class Terms:
    """What a chain charges to bring the order to your door."""

    chain: str
    delivery: float
    minimum_order: float
    free_above: float | None = None  # delivery waived once the subtotal reaches this

    def delivery_for(self, subtotal: float) -> float:
        if self.free_above is not None and subtotal >= self.free_above:
            return 0.0
        return self.delivery


# Starting values, checked 2026-09-03 against the chains' own pages and press
# coverage of Mercadona's February 2025 fee change. They differ by postcode and
# change without notice, so the CLI lets you override every one of them.
#   Mercadona: 8,20 EUR delivery, 60 EUR minimum (ayuda.tienda.mercadona.es).
#   Dia: 4,99 EUR delivery, free from 100 EUR, no stated minimum (dia.es).
DEFAULT_TERMS: dict[str, tuple[float, float, float | None]] = {
    "mercadona": (8.20, 60.0, None),
    "dia": (4.99, 0.0, 100.0),
}


@dataclass(slots=True)
class Plan:
    chains: tuple[str, ...]
    assignment: dict[int, str] = field(default_factory=dict)
    subtotals: dict[str, float] = field(default_factory=dict)
    delivery: float = 0.0
    split_penalty: float = 0.0
    feasible: bool = True
    reason: str = ""

    @property
    def products(self) -> float:
        return round(sum(self.subtotals.values()), 2)

    @property
    def total(self) -> float:
        return round(self.products + self.delivery + self.split_penalty, 2)

    @property
    def label(self) -> str:
        if len(self.chains) == 1:
            return f"todo en {self.chains[0]}"
        return "repartido entre " + " y ".join(self.chains)


def parse_list(text: str) -> list[tuple[str, int]]:
    """One item per line; a quantity may lead ("2x leche") or trail ("leche x2")."""
    items = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        quantity = 1
        if match := _QTY_PREFIX.match(line):
            quantity, line = int(match.group(1)), match.group(2)
        elif match := _QTY_SUFFIX.match(line):
            line, quantity = match.group(1), int(match.group(2))
        items.append((line.strip(), max(quantity, 1)))
    return items


def _offer(record: dict[str, Any], chain: str) -> Offer:
    return Offer(
        chain=chain,
        sku=str(record["sku"]),
        name=record["display_name"],
        unit_price=float(record["unit_price"]),
        reference_price=record.get("reference_price"),
        reference_format=record.get("reference_format"),
    )


def build_offers(
    records_a: list[dict[str, Any]],
    records_b: list[dict[str, Any]],
    equivalences: list[dict[str, Any]],
    chain_a: str,
    chain_b: str,
) -> tuple[list[tuple[dict[str, Offer], str]], list[Offer]]:
    """Group products into cross-chain pairs and chain-locked singles.

    A pair is two offers the matcher judged the same or substitutable, tagged
    with how it knew. Everything unpaired is a single: real, buyable, but only
    at its own chain.
    """
    by_a = {str(r["sku"]): r for r in records_a}
    by_b = {str(r["sku"]): r for r in records_b}
    pairs: list[tuple[dict[str, Offer], str]] = []
    paired_a: set[str] = set()
    paired_b: set[str] = set()
    for equivalence in equivalences:
        sku_a, sku_b = str(equivalence["a"]["sku"]), str(equivalence["b"]["sku"])
        if sku_a not in by_a or sku_b not in by_b:
            continue  # delisted since the pairing was made
        pairs.append((
            {chain_a: _offer(by_a[sku_a], chain_a), chain_b: _offer(by_b[sku_b], chain_b)},
            equivalence.get("method", "paired"),
        ))
        paired_a.add(sku_a)
        paired_b.add(sku_b)
    singles = [_offer(r, chain_a) for s, r in by_a.items() if s not in paired_a]
    singles += [_offer(r, chain_b) for s, r in by_b.items() if s not in paired_b]
    return pairs, singles


def _match_score(query: str, query_tokens: frozenset[str], name: str) -> tuple[float, bool, float]:
    """How well a product name answers a query, as (coverage, numbers_ok, jaccard).

    Coverage — the share of the query's words the name contains — decides.
    "leche semidesnatada" finds "Leche semidesnatada Hacendado" (full coverage)
    ahead of "Leche" (half). Then the query's standalone numbers must all appear
    in the name: word tokens drop single digits, so without this every nappy
    size ties and "talla 4" can come back as talla 2. Jaccard is only a late
    tie-break towards the shorter, more specific name: it must never outrank a
    cross-chain pair, or a lone product with neater wording steals the choice.
    """
    if not query_tokens:
        return (0.0, False, 0.0)
    name_tokens = tokenize(name)
    coverage = len(query_tokens & name_tokens) / len(query_tokens)
    # A name that states no numbers is neutral, not a mismatch: Mercadona keeps
    # sizes out of its titles entirely, so "leche 6 L" must still reach the
    # Hacendado side of a pair. Only a name that does state numbers can disagree.
    name_numbers = standalone_numbers(name)
    numbers_ok = not name_numbers or standalone_numbers(query) <= name_numbers
    return (round(coverage, 4), numbers_ok, score_pair(query_tokens, name_tokens))


def find_item(
    query: str,
    quantity: int,
    pairs: list[tuple[dict[str, Offer], str]],
    singles: list[Offer],
) -> Item | None:
    """The product (or cross-chain pair) that best answers one line of the list.

    A size in the query ("aceite 1 L") is honoured when any candidate has it;
    otherwise all sizes compete and the shopper is told which one was picked.
    Pairs are preferred to singles at equal score: a pair gives a choice.
    """
    query_tokens = tokenize(query)
    wanted_size = parse_size(query)

    candidates: list[tuple[tuple[float, bool, float], bool, dict[str, Offer], str]] = []
    for offers, method in pairs:
        scores = [_match_score(query, query_tokens, o.name) for o in offers.values()]
        # Coverage and wording take the better side; the query's numbers must
        # hold on EVERY side. A pair that wrongly joins a size-4 nappy to a
        # size-2 one would otherwise pass on the strength of its good half.
        best_side = max(scores)
        score = (best_side[0], all(sc[1] for sc in scores), best_side[2])
        fits = any(_size_fits(o, wanted_size) for o in offers.values())
        candidates.append((score, fits, offers, method))
    for offer in singles:
        score = _match_score(query, query_tokens, offer.name)
        candidates.append((score, _size_fits(offer, wanted_size), {offer.chain: offer}, "single"))

    if not candidates:
        return None
    if wanted_size and any(fits for _, fits, _, _ in candidates):
        candidates = [c for c in candidates if c[1]]
    # Coverage; then the query's numbers present; then a pair (a choice) beats a
    # single; then wording; then price.
    best = max(
        candidates,
        key=lambda c: (c[0][0], c[0][1], len(c[2]) > 1, c[0][2],
                       -min(o.unit_price for o in c[2].values())),
    )
    (coverage, _, _), _, offers, method = best
    if coverage < MIN_COVERAGE:
        return None
    return Item(query=query, quantity=quantity, offers=dict(offers), method=method,
                score=coverage)


def _size_fits(offer: Offer, wanted: tuple[float, str] | None) -> bool:
    if wanted is None:
        return True
    actual = record_size({
        "unit_price": offer.unit_price,
        "reference_price": offer.reference_price,
        "reference_format": offer.reference_format,
        "display_name": offer.name,
    })
    return sizes_agree(actual, wanted)


def optimize(
    items: list[Item],
    terms: dict[str, Terms],
    split_penalty: float = 0.0,
) -> list[Plan]:
    """Every way of placing the order, best first, each one costed and explained.

    With `n` chains the plans are the non-empty subsets of chains. Within a
    plan each item goes to its cheapest chain among those used; a chain whose
    subtotal then falls short of its minimum order pulls the cheapest-to-move
    items over until it clears the bar, and if it cannot, the plan says so
    rather than pretending the order could be placed.
    """
    chains = sorted(terms)
    plans: list[Plan] = []
    for size in range(1, len(chains) + 1):
        for used in combinations(chains, size):
            plans.append(_plan(items, used, terms, split_penalty if size > 1 else 0.0))
    plans.sort(key=lambda p: (not p.feasible, p.total))
    return plans


def _plan(
    items: list[Item], used: tuple[str, ...], terms: dict[str, Terms], split_penalty: float
) -> Plan:
    plan = Plan(chains=used, split_penalty=split_penalty)
    plan.subtotals = {chain: 0.0 for chain in used}

    for index, item in enumerate(items):
        options = [chain for chain in used if chain in item.offers]
        if not options:
            plan.feasible = False
            plan.reason = f"«{item.query}» no está en {' ni '.join(used)}"
            plan.subtotals = {chain: 0.0 for chain in used}  # nothing to price
            return plan
        chain = min(options, key=lambda c: item.offers[c].unit_price)
        plan.assignment[index] = chain
        plan.subtotals[chain] = round(plan.subtotals[chain] + item.cost(chain), 2)

    _meet_minimums(items, plan, terms)
    plan.delivery = round(
        sum(terms[c].delivery_for(plan.subtotals[c]) for c in used if plan.subtotals[c] > 0), 2
    )
    return plan


def _meet_minimums(items: list[Item], plan: Plan, terms: dict[str, Terms]) -> None:
    """Shift items so every used chain reaches its minimum order, if it can.

    Moves the item whose transfer costs least first. Only applies when there
    is another chain to take items from; a single-chain plan below minimum is
    simply not placeable.
    """
    for chain in plan.chains:
        shortfall = terms[chain].minimum_order - plan.subtotals[chain]
        if shortfall <= 0:
            continue
        donors = [c for c in plan.chains if c != chain]
        movable = [
            (item.cost(chain) - item.cost(plan.assignment[i]), i)
            for i, item in enumerate(items)
            if plan.assignment[i] in donors and chain in item.offers
        ]
        movable.sort()
        for extra, index in movable:
            if plan.subtotals[chain] >= terms[chain].minimum_order:
                break
            source = plan.assignment[index]
            plan.subtotals[source] = round(plan.subtotals[source] - items[index].cost(source), 2)
            plan.subtotals[chain] = round(plan.subtotals[chain] + items[index].cost(chain), 2)
            plan.assignment[index] = chain
        if plan.subtotals[chain] < terms[chain].minimum_order:
            plan.feasible = False
            plan.reason = (
                f"{chain} no llega al pedido mínimo de {eur(terms[chain].minimum_order)} "
                f"(subtotal {eur(plan.subtotals[chain])})"
            )
            return
    # Dropping every item from a chain leaves it unused: no delivery, no minimum.
    for chain in list(plan.subtotals):
        if plan.subtotals[chain] == 0 and any(
            plan.assignment[i] == chain for i in plan.assignment
        ):
            plan.subtotals[chain] = 0.0


def _per_unit_note(item: Item) -> str:
    """For counted packs of different sizes, the fair figure is the price per unit.

    A 58-nappy pack and a 62-nappy pack are the same product; the pack price
    says which costs more at the till, the per-unit price says which is dearer.
    """
    offers = list(item.offers.values())
    if any(o.reference_format != "ud" or not o.reference_price for o in offers):
        return ""
    counts = {round(o.unit_price / o.reference_price) for o in offers}
    if len(counts) < 2:
        return ""
    per_unit = ", ".join(f"{o.chain} {eur(o.reference_price)}/ud" for o in offers)
    return f" ({per_unit})"


def eur(amount: float) -> str:
    return f"{amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"


def explain(items: list[Item], plans: list[Plan], terms: dict[str, Terms],
            not_found: list[tuple[str, int]]) -> str:
    """The whole decision in plain Spanish, euros and reasons included."""
    lines: list[str] = []
    paired = sum(1 for i in items if len(i.offers) > 1)
    lines.append(
        f"Tu lista: {len(items)} productos encontrados "
        f"({paired} comparables entre cadenas, {len(items) - paired} solo en una)"
    )
    for query, _ in not_found:
        lines.append(f"  no encontrado: «{query}»")
    lines.append("")

    for plan in plans:
        if not plan.feasible and plan.products == 0:
            lines.append(f"  {plan.label:<34} {'—':>10}             ✗ {plan.reason}")
            continue
        head = f"  {plan.label:<34} {eur(plan.products):>10} productos"
        if plan.delivery:
            head += f" + {eur(plan.delivery)} envío"
        if plan.split_penalty:
            head += f" + {eur(plan.split_penalty)} por repartir"
        head += f" = {eur(plan.total)}"
        if not plan.feasible:
            head += f"   ✗ {plan.reason}"
        lines.append(head)

    feasible = [p for p in plans if p.feasible]
    if not feasible:
        lines.append("\nNingún plan es viable con estas condiciones.")
        return "\n".join(lines)

    best = feasible[0]
    lines.append(f"\nMejor: {best.label} por {eur(best.total)}")
    singles = [p for p in feasible if len(p.chains) == 1]
    split = [p for p in feasible if len(p.chains) > 1]
    if singles and split:
        one, many = min(singles, key=lambda p: p.total), min(split, key=lambda p: p.total)
        saved = round(one.products - many.products, 2)
        extra = round((many.delivery + many.split_penalty) - one.delivery, 2)
        verdict = "compensa" if many.total < one.total else "no compensa"
        lines.append(
            f"Repartir ahorra {eur(saved)} en productos pero cuesta {eur(extra)} más "
            f"de envío → {verdict}."
        )

    lines.append("\nPor producto:")
    chains = sorted(terms)
    for index, item in enumerate(items):
        cells = []
        for chain in chains:
            cost = item.cost(chain)
            cells.append(f"{chain} {eur(cost):>9}" if cost is not None else f"{chain}       —  ")
        marker = ""
        if len(item.offers) > 1:
            costs = {c: item.cost(c) for c in item.offers}
            cheap, dear = min(costs, key=costs.get), max(costs, key=costs.get)
            gap = round(costs[dear] - costs[cheap], 2)
            marker = "  igual" if gap < 0.01 else f"  {cheap} ahorra {eur(gap)}"
            marker += _per_unit_note(item)
        qty = f"{item.quantity}x " if item.quantity > 1 else ""
        chosen = item.offers[best.assignment[index]].name
        lines.append(f"  {qty}{item.query[:28]:<30} {'   '.join(cells)}{marker}")
        lines.append(f"      → {chosen[:70]}")
    return "\n".join(lines)
