import pytest

from opencesta.basket import (
    Item,
    Offer,
    Terms,
    build_offers,
    explain,
    find_item,
    optimize,
    parse_list,
)


def offer(chain, sku, name, price, ref=None, fmt="L"):
    return Offer(chain=chain, sku=sku, name=name, unit_price=price,
                 reference_price=ref if ref is not None else price, reference_format=fmt)


def item(query, offers, qty=1, method="brand-size-name"):
    return Item(query=query, quantity=qty, offers={o.chain: o for o in offers},
                method=method, score=1.0)


TERMS = {
    "mercadona": Terms("mercadona", delivery=7.21, minimum_order=50.0),
    "dia": Terms("dia", delivery=4.99, minimum_order=20.0),
}


# --- parsing --------------------------------------------------------------

@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("leche", [("leche", 1)]),
        ("2x leche", [("leche", 2)]),
        ("2 x leche", [("leche", 2)]),
        ("leche x2", [("leche", 2)]),
        ("# comentario\n\nleche\n3x huevos\n", [("leche", 1), ("huevos", 3)]),
        ("0x leche", [("leche", 1)]),  # a zero quantity is a typo, not an order
    ],
)
def test_parse_list(text, expected):
    assert parse_list(text) == expected


# --- building offers ------------------------------------------------------

def record(sku, name, price, ref=None, fmt="L"):
    return {"sku": sku, "display_name": name, "unit_price": price,
            "reference_price": ref if ref is not None else price, "reference_format": fmt}


def test_build_offers_splits_pairs_from_singles():
    a = [record("m1", "Leche Hacendado", 1.0), record("m2", "Solo en Mercadona", 2.0)]
    b = [record("d1", "Leche Dia", 1.1)]
    eq = [{"a": {"sku": "m1"}, "b": {"sku": "d1"}, "method": "own-brand-equivalent"}]

    pairs, singles = build_offers(a, b, eq, "mercadona", "dia")

    assert len(pairs) == 1
    assert set(pairs[0][0]) == {"mercadona", "dia"}
    assert pairs[0][1] == "own-brand-equivalent"
    assert [s.sku for s in singles] == ["m2"]


def test_build_offers_drops_pairs_whose_product_left_the_catalog():
    a = [record("m1", "Leche", 1.0)]
    eq = [{"a": {"sku": "m1"}, "b": {"sku": "gone"}, "method": "x"}]
    pairs, singles = build_offers(a, [], eq, "mercadona", "dia")
    assert pairs == []
    assert [s.sku for s in singles] == ["m1"]  # still buyable, just not comparable


# --- finding items --------------------------------------------------------

def test_find_prefers_full_coverage_over_partial():
    pairs = [
        ({"mercadona": offer("mercadona", "1", "Leche Hacendado", 1.0),
          "dia": offer("dia", "2", "Leche Dia", 1.0)}, "x"),
        ({"mercadona": offer("mercadona", "3", "Leche semidesnatada Hacendado", 1.2),
          "dia": offer("dia", "4", "Leche semidesnatada Dia", 1.2)}, "x"),
    ]
    found = find_item("leche semidesnatada", 1, pairs, [])
    assert found.offers["mercadona"].sku == "3"


def test_find_honours_a_size_in_the_query():
    pairs = [
        ({"mercadona": offer("mercadona", "1", "Leche Hacendado", 1.17, ref=1.17),
          "dia": offer("dia", "2", "Leche Dia 1 L", 1.17, ref=1.17)}, "x"),
        ({"mercadona": offer("mercadona", "3", "Leche Hacendado", 7.02, ref=1.17),
          "dia": offer("dia", "4", "Leche Dia pack 6 x 1 L", 7.02, ref=1.17)}, "x"),
    ]
    assert find_item("leche 6 L", 1, pairs, []).offers["mercadona"].sku == "3"
    assert find_item("leche 1 L", 1, pairs, []).offers["mercadona"].sku == "1"


def test_find_prefers_a_pair_over_a_single_at_equal_score():
    pairs = [({"mercadona": offer("mercadona", "1", "Aceite oliva", 4.0),
               "dia": offer("dia", "2", "Aceite oliva", 4.1)}, "x")]
    singles = [offer("mercadona", "9", "Aceite oliva", 3.0)]
    assert len(find_item("aceite oliva", 1, pairs, singles).offers) == 2


def test_a_pair_beats_a_single_with_neater_wording():
    """The single has the tighter name; the pair gives a choice, and choice wins."""
    pairs = [({"mercadona": offer("mercadona", "1", "Arroz basmati aromático Hacendado", 2.0),
               "dia": offer("dia", "2", "Arroz basmati Dia Selección Mundial 1 Kg", 2.1)},
              "own-brand-equivalent")]
    singles = [offer("dia", "9", "Arroz basmati 1 kg", 1.9)]
    found = find_item("arroz basmati", 1, pairs, singles)
    assert len(found.offers) == 2


def test_strictly_better_coverage_still_beats_a_pair():
    pairs = [({"mercadona": offer("mercadona", "1", "Arroz Hacendado", 2.0),
               "dia": offer("dia", "2", "Arroz Dia", 2.1)}, "x")]
    singles = [offer("dia", "9", "Arroz basmati integral", 1.9)]
    found = find_item("arroz basmati integral", 1, pairs, singles)
    assert found.offers["dia"].sku == "9"


def test_a_number_in_the_query_picks_the_right_variant():
    """Word tokens drop single digits, so every nappy size used to tie."""
    singles = [
        offer("dia", "1", "Pañales 4-8 kg talla 2 Dodot 58 unidades", 18.95, fmt="ud"),
        offer("dia", "2", "Pañales 8-15 kg talla 4 Dia Planeta Bebé 44 unidades", 20.95,
              fmt="ud"),
    ]
    assert find_item("pañales talla 4", 1, [], singles).offers["dia"].sku == "2"
    assert find_item("pañales talla 2", 1, [], singles).offers["dia"].sku == "1"


def test_a_side_with_no_numbers_is_neutral_not_a_mismatch():
    """Mercadona titles carry no size; "leche 6 L" must still reach its pair."""
    pairs = [({"mercadona": offer("mercadona", "1", "Leche semidesnatada Hacendado", 7.02,
                                  ref=1.17),
               "dia": offer("dia", "2", "Leche semidesnatada Dia Láctea pack 6 x 1 L", 7.02,
                            ref=1.17)}, "own-brand-substitute")]
    singles = [offer("dia", "9", "Leche semidesnatada Celta pack 6 x 1 L", 6.30, ref=1.05)]
    found = find_item("leche semidesnatada 6 L", 1, pairs, singles)
    assert len(found.offers) == 2


def test_a_pair_must_satisfy_the_query_numbers_on_both_sides():
    """A bad pair joining talla 4 to talla 2 must not win on its good half."""
    bad_pair = [({"mercadona": offer("mercadona", "1", "Pañales talla 4 Deliplus", 18.95,
                                     fmt="ud"),
                  "dia": offer("dia", "2", "Pañales 4-8 kg talla 2 Dodot 58 unidades", 20.95,
                               fmt="ud")}, "own-brand-substitute")]
    right_single = [offer("dia", "9", "Pañales 8-15 kg talla 4 Dia Planeta Bebé 44 unidades",
                          20.95, fmt="ud")]
    found = find_item("pañales talla 4", 1, bad_pair, right_single)
    assert found.offers["dia"].sku == "9"


def test_find_returns_none_below_minimum_coverage():
    singles = [offer("mercadona", "1", "Leche Hacendado", 1.0)]
    assert find_item("pañales talla cuatro", 1, [], singles) is None


def test_find_carries_the_quantity():
    singles = [offer("mercadona", "1", "Leche", 1.0)]
    assert find_item("leche", 3, [], singles).quantity == 3


# --- optimizing -----------------------------------------------------------

def test_each_item_goes_to_its_cheapest_chain_in_a_split():
    items = [
        item("leche", [offer("mercadona", "1", "Leche", 1.0), offer("dia", "2", "Leche", 1.5)]),
        item("pan", [offer("mercadona", "3", "Pan", 2.0), offer("dia", "4", "Pan", 1.0)]),
    ]
    terms = {c: Terms(c, delivery=0.0, minimum_order=0.0) for c in ("mercadona", "dia")}
    split = next(p for p in optimize(items, terms) if len(p.chains) == 2)
    assert split.assignment == {0: "mercadona", 1: "dia"}
    assert split.products == 2.0


def test_delivery_can_make_the_single_chain_plan_win():
    """The split saves 0,40 in products but pays a second delivery."""
    items = [
        item("leche", [offer("mercadona", "1", "Leche", 1.0), offer("dia", "2", "Leche", 1.5)]),
        item("pan", [offer("mercadona", "3", "Pan", 1.0), offer("dia", "4", "Pan", 0.6)]),
    ]
    terms = {
        "mercadona": Terms("mercadona", delivery=5.0, minimum_order=0.0),
        "dia": Terms("dia", delivery=5.0, minimum_order=0.0),
    }
    best = optimize(items, terms)[0]
    assert best.chains == ("mercadona",)
    assert best.total == 7.0  # 2.00 + 5.00; all-Dia 7.10; split 1.60 + 10.00


def test_an_exact_tie_is_broken_deterministically():
    """Same total everywhere: the answer must be stable, not depend on dict order."""
    items = [item("x", [offer("mercadona", "1", "X", 1.0), offer("dia", "2", "X", 1.0)])]
    terms = {c: Terms(c, delivery=0.0, minimum_order=0.0) for c in ("mercadona", "dia")}
    assert optimize(items, terms)[0].chains == ("dia",)  # alphabetical, single chain


def test_quantity_multiplies_the_cost():
    items = [item("leche", [offer("mercadona", "1", "Leche", 1.0)], qty=6)]
    terms = {"mercadona": Terms("mercadona", delivery=0.0, minimum_order=0.0)}
    assert optimize(items, terms)[0].products == 6.0


def test_a_chain_below_its_minimum_pulls_items_over():
    items = [
        item("a", [offer("mercadona", "1", "A", 10.0), offer("dia", "2", "A", 11.0)]),
        item("b", [offer("mercadona", "3", "B", 10.0), offer("dia", "4", "B", 12.0)]),
        item("c", [offer("mercadona", "5", "C", 10.0), offer("dia", "6", "C", 5.0)]),
    ]
    terms = {
        "mercadona": Terms("mercadona", delivery=0.0, minimum_order=0.0),
        "dia": Terms("dia", delivery=0.0, minimum_order=15.0),
    }
    split = next(p for p in optimize(items, terms) if len(p.chains) == 2)
    # Greedy: only c goes to dia (5.00) — below 15.00. The cheapest move is a
    # (+1.00), which lifts dia to 16.00.
    assert split.feasible
    assert split.assignment == {0: "dia", 1: "mercadona", 2: "dia"}
    assert split.subtotals == {"mercadona": 10.0, "dia": 16.0}


def test_an_unreachable_minimum_marks_the_plan_infeasible_not_wrong():
    items = [item("leche", [offer("mercadona", "1", "Leche", 1.0)])]
    terms = {"mercadona": Terms("mercadona", delivery=0.0, minimum_order=50.0)}
    plan = optimize(items, terms)[0]
    assert not plan.feasible
    assert "pedido mínimo" in plan.reason


def test_a_single_chain_item_forces_that_chain():
    items = [item("solo", [offer("mercadona", "1", "Solo", 3.0)], method="single")]
    terms = {c: Terms(c, delivery=0.0, minimum_order=0.0) for c in ("mercadona", "dia")}
    plans = optimize(items, terms)
    dia_only = next(p for p in plans if p.chains == ("dia",))
    assert not dia_only.feasible
    assert "no está en dia" in dia_only.reason


def test_split_penalty_is_charged_only_when_splitting():
    items = [item("x", [offer("mercadona", "1", "X", 1.0), offer("dia", "2", "X", 1.0)])]
    terms = {c: Terms(c, delivery=0.0, minimum_order=0.0) for c in ("mercadona", "dia")}
    plans = {p.chains: p for p in optimize(items, terms, split_penalty=3.0)}
    assert plans[("mercadona",)].split_penalty == 0.0
    assert plans[("dia", "mercadona")].split_penalty == 3.0


def test_feasible_plans_sort_before_infeasible_ones():
    items = [item("solo", [offer("mercadona", "1", "Solo", 3.0)], method="single")]
    terms = {c: Terms(c, delivery=0.0, minimum_order=0.0) for c in ("mercadona", "dia")}
    plans = optimize(items, terms)
    assert plans[0].feasible and not plans[-1].feasible


# --- explaining -----------------------------------------------------------

def test_explanation_states_the_verdict_in_euros():
    items = [
        item("leche", [offer("mercadona", "1", "Leche", 1.0), offer("dia", "2", "Leche", 1.5)]),
        item("pan", [offer("mercadona", "3", "Pan", 1.0), offer("dia", "4", "Pan", 0.6)]),
    ]
    terms = {
        "mercadona": Terms("mercadona", delivery=5.0, minimum_order=0.0),
        "dia": Terms("dia", delivery=5.0, minimum_order=0.0),
    }
    text = explain(items, optimize(items, terms), terms, not_found=[("cosa rara", 1)])

    assert "Mejor: todo en mercadona por 7,00 €" in text
    assert "14,78" not in text  # no partial figure anywhere
    assert "no compensa" in text
    assert "no encontrado: «cosa rara»" in text
    assert "dia ahorra 0,40 €" in text  # the per-item line for pan


def test_delivery_is_waived_above_the_free_threshold():
    items = [item("x", [offer("mercadona", "1", "X", 120.0), offer("dia", "2", "X", 120.0)])]
    terms = {
        "mercadona": Terms("mercadona", delivery=8.20, minimum_order=0.0),
        "dia": Terms("dia", delivery=4.99, minimum_order=0.0, free_above=100.0),
    }
    plans = {p.chains: p for p in optimize(items, terms)}
    assert plans[("dia",)].delivery == 0.0
    assert plans[("mercadona",)].delivery == 8.20
    assert plans[("dia",)].total < plans[("mercadona",)].total
