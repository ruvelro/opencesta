import pytest

from opencesta.match import (
    is_multipack,
    is_own_brand,
    load_overrides,
    match_records,
    own_brand_candidates,
    parse_size,
    record_size,
    resolve_conflicts,
    score_pair,
    size_gap,
    sizes_agree,
    tokenize,
    write_equivalences,
)


def product(sku, name, brand, fmt="L", unit_size=None, size_format=None):
    return {
        "sku": sku,
        "display_name": name,
        "brand": brand,
        "reference_format": fmt,
        "unit_size": unit_size,
        "size_format": size_format,
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("pack 6 x 1 L", (6.0, "L")),
        ("12 x 330 ml", (3.96, "L")),
        ("330 g", (0.33, "kg")),
        ("1,5 L", (1.5, "L")),
        ("4 x 120 g", (0.48, "kg")),
        ("2 x 2 L", (4.0, "L")),
        ("sin tamaño alguno", None),
    ],
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


def test_tokenize_drops_brand_size_and_stopwords():
    tokens = tokenize("Leche semidesnatada Asturiana pack 6 x 1 L", "Asturiana")
    assert tokens == frozenset({"leche", "semidesnatada"})


def test_tokenize_handles_accents_and_multiword_brand():
    tokens = tokenize("Refresco Coca-Cola zero azúcar 2 x 2 L", "Coca-Cola")
    assert tokens == frozenset({"refresco", "zero", "azucar"})


def test_record_size_prefers_explicit_fields():
    """Mercadona keeps size out of the title; without the fields its rows collide."""
    record = product("1", "Leche semidesnatada Asturiana", "Asturiana",
                     unit_size=6.0, size_format="l")
    assert record_size(record) == (6.0, "L")


def test_record_size_falls_back_to_the_title():
    record = product("2", "Leche semidesnatada Asturiana pack 6 x 1 L", "Asturiana")
    assert record_size(record) == (6.0, "L")


def test_score_pair():
    assert score_pair(frozenset({"a", "b"}), frozenset({"a", "b"})) == 1.0
    assert score_pair(frozenset({"a", "b"}), frozenset({"a", "c"})) == 0.333
    assert score_pair(frozenset(), frozenset({"a"})) == 0.0


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((6.0, "L"), (6.0, "L"), True),
        ((6.0, "L"), (6.05, "L"), True),  # rounding
        ((6.0, "L"), (1.0, "L"), False),  # different pack
        ((1.0, "L"), (1.0, "kg"), False),  # different unit
        (None, (1.0, "L"), False),
    ],
)
def test_sizes_agree(a, b, expected):
    assert sizes_agree(a, b) is expected


def test_match_pairs_same_product_across_chains():
    mercadona = [product("m1", "Leche semidesnatada Asturiana", "Asturiana",
                         unit_size=6.0, size_format="l")]
    dia = [product("d1", "Leche semidesnatada Asturiana pack 6 x 1 L", "Asturiana")]

    matches = match_records(mercadona, dia)
    assert len(matches) == 1
    assert (matches[0].sku_a, matches[0].sku_b) == ("m1", "d1")
    assert matches[0].score == 1.0
    assert matches[0].size == 6.0
    assert matches[0].shared_terms == ("leche", "semidesnatada")


def test_size_separates_mercadona_rows_sharing_a_name():
    """Four Mercadona rows share this exact title; only the 6 L one is the match."""
    mercadona = [
        product("m9", "Leche semidesnatada Asturiana", "Asturiana", unit_size=9.0, size_format="l"),
        product("m6", "Leche semidesnatada Asturiana", "Asturiana", unit_size=6.0, size_format="l"),
        product("m1", "Leche semidesnatada Asturiana", "Asturiana", unit_size=1.0, size_format="l"),
    ]
    dia = [product("d6", "Leche semidesnatada Asturiana pack 6 x 1 L", "Asturiana")]

    matches = match_records(mercadona, dia)
    assert [m.sku_a for m in matches] == ["m6"]


def test_each_product_is_used_at_most_once():
    mercadona = [product("m1", "Yogur natural Danone", "Danone", fmt="kg",
                         unit_size=480.0, size_format="g")]
    dia = [
        product("d1", "Yogur natural Danone 4 x 120 g", "Danone", fmt="kg"),
        product("d2", "Yogur natural azucarado Danone 4 x 120 g", "Danone", fmt="kg"),
    ]

    matches = match_records(mercadona, dia)
    assert len(matches) == 1
    assert matches[0].sku_b == "d1"  # the exact-wording pair wins over the variant


def test_different_brands_never_match():
    matches = match_records(
        [product("m1", "Leche entera Asturiana", "Asturiana", unit_size=1.0, size_format="l")],
        [product("d1", "Leche entera Puleva", "Puleva", unit_size=1.0, size_format="l")],
    )
    assert matches == []


def test_different_reference_formats_never_match():
    matches = match_records(
        [product("m1", "Tomate frito Orlando", "Orlando", fmt="kg",
                 unit_size=400.0, size_format="g")],
        [product("d1", "Tomate frito Orlando 400 g", "Orlando", fmt="L")],
    )
    assert matches == []


def test_low_overlap_is_rejected():
    matches = match_records(
        [product("m1", "Leche entera Asturiana", "Asturiana", unit_size=1.0, size_format="l")],
        [product("d1", "Batido de chocolate Asturiana 1 L", "Asturiana")],
    )
    assert matches == []


def test_price_is_not_an_input():
    """Equivalence must not depend on price, or 'cheaper at X' becomes unfalsifiable."""
    cheap = product("m1", "Leche entera Asturiana", "Asturiana", unit_size=1.0, size_format="l")
    dear = product("d1", "Leche entera Asturiana 1 L", "Asturiana")
    cheap["unit_price"], cheap["reference_price"] = 0.50, 0.50
    dear["unit_price"], dear["reference_price"] = 9.99, 9.99

    matches = match_records([cheap], [dear])
    assert len(matches) == 1
    assert matches[0].score == 1.0


def test_override_forces_a_pair(tmp_path):
    path = tmp_path / "equivalences.jsonl"
    path.write_text(
        '{"verdict": "equivalent", "a": {"sku": "m1"}, "b": {"sku": "d1"}, "note": "mismo bote"}\n',
        encoding="utf-8",
    )
    mercadona = [product("m1", "Tomate frito", "Orlando", fmt="kg")]
    dia = [product("d1", "Salsa de tomate estilo casero", "Orlando", fmt="kg")]

    matches = match_records(mercadona, dia, overrides=load_overrides(path))
    assert len(matches) == 1
    assert matches[0].method == "community-override"
    assert matches[0].shared_terms == ("mismo bote",)


def test_override_forbids_a_pair(tmp_path):
    path = tmp_path / "equivalences.jsonl"
    path.write_text(
        '{"verdict": "different", "a": {"sku": "m1"}, "b": {"sku": "d1"}}\n', encoding="utf-8"
    )
    mercadona = [product("m1", "Leche entera Asturiana", "Asturiana",
                         unit_size=1.0, size_format="l")]
    dia = [product("d1", "Leche entera Asturiana 1 L", "Asturiana")]

    assert match_records(mercadona, dia) != []  # would match without the override
    assert match_records(mercadona, dia, overrides=load_overrides(path)) == []


def test_override_for_a_delisted_product_is_skipped(tmp_path):
    path = tmp_path / "equivalences.jsonl"
    path.write_text(
        '{"verdict": "equivalent", "a": {"sku": "gone"}, "b": {"sku": "d1"}}\n', encoding="utf-8"
    )
    matches = match_records([], [product("d1", "x", "B")], overrides=load_overrides(path))
    assert matches == []


def test_missing_overrides_file_is_fine(tmp_path):
    assert load_overrides(tmp_path / "nope.jsonl") == ({}, set())


def test_comments_and_blank_lines_are_ignored(tmp_path):
    path = tmp_path / "e.jsonl"
    path.write_text(
        '# una nota\n\n{"verdict": "different", "a": {"sku": "1"}, "b": {"sku": "2"}}\n',
        encoding="utf-8",
    )
    assert load_overrides(path) == ({}, {("1", "2")})


def test_malformed_override_names_the_line(tmp_path):
    path = tmp_path / "e.jsonl"
    path.write_text('{"verdict": "equivalent"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="e.jsonl:1"):
        load_overrides(path)


def test_unknown_verdict_is_rejected(tmp_path):
    path = tmp_path / "e.jsonl"
    path.write_text('{"verdict": "quizas", "a": {"sku": "1"}, "b": {"sku": "2"}}\n', "utf-8")
    with pytest.raises(ValueError, match="verdict must be"):
        load_overrides(path)


def test_write_equivalences_roundtrip(tmp_path):
    import json as jsonlib

    mercadona = [product("m1", "Leche entera Asturiana", "Asturiana",
                         unit_size=1.0, size_format="l")]
    dia = [product("d1", "Leche entera Asturiana 1 L", "Asturiana")]
    path = write_equivalences(match_records(mercadona, dia), tmp_path / "out.jsonl")

    rows = [jsonlib.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1
    assert rows[0]["a"]["sku"] == "m1"
    assert rows[0]["b"]["sku"] == "d1"
    assert rows[0]["method"] == "brand-size-name"


def test_is_own_brand_by_mercadona_ean_prefix():
    assert is_own_brand("mercadona", {"ean": "8480000100054", "brand": "Hacendado"})
    assert not is_own_brand("mercadona", {"ean": "8411700005837", "brand": "Puleva"})


def test_is_own_brand_by_dia_name_prefix():
    assert is_own_brand("dia", {"brand": "Dia Láctea"})
    assert is_own_brand("dia", {"brand": "Diasol"})
    assert not is_own_brand("dia", {"brand": "Danone"})


def test_unbranded_products_count_as_own_brand():
    """Unbranded staples (fruit, bakery) are exactly what we want to compare."""
    assert is_own_brand("mercadona", {"brand": None, "ean": None})
    assert is_own_brand("dia", {"brand": ""})


def test_own_brand_candidates_ignore_brand_but_not_size():
    hacendado = product("m1", "Leche entera Hacendado", "Hacendado",
                        unit_size=1.0, size_format="l")
    same_size = product("d1", "Leche entera Dia Láctea 1 L", "Dia Láctea")
    other_size = product("d2", "Leche entera Dia Láctea pack 6 x 1 L", "Dia Láctea")

    candidates = own_brand_candidates([hacendado], [same_size, other_size])
    assert [c[2]["sku"] for c in candidates] == ["d1"]
    assert candidates[0][0] == 1.0


def test_own_brand_candidates_are_sorted_best_first():
    mercadona = [product("m1", "Tomate frito Hacendado", "Hacendado",
                         fmt="kg", unit_size=400.0, size_format="g")]
    dia = [
        product("d1", "Tomate frito con aceite de oliva Dia 400 g", "Dia", fmt="kg"),
        product("d2", "Tomate frito Dia 400 g", "Dia", fmt="kg"),
    ]
    scores = [c[0] for c in own_brand_candidates(mercadona, dia)]
    assert scores == sorted(scores, reverse=True)


def test_resolve_conflicts_keeps_the_best_free_pair():
    a1 = product("m1", "x", "B")
    b1, b2 = product("d1", "x", "B"), product("d2", "x", "B")
    candidates = [(0.9, a1, b1, frozenset()), (0.8, a1, b2, frozenset())]

    kept = resolve_conflicts(candidates, lambda *_: True)
    assert [k[2]["sku"] for k in kept] == ["d1"]


def test_resolve_conflicts_does_not_consult_accept_for_claimed_products():
    """A judge must never be paid to rule on a pair a better one already won."""
    a1 = product("m1", "x", "B")
    b1, b2 = product("d1", "x", "B"), product("d2", "x", "B")
    asked = []

    def accept(score, x, y):
        asked.append(y["sku"])
        return True

    resolve_conflicts([(0.9, a1, b1, frozenset()), (0.8, a1, b2, frozenset())], accept)
    assert asked == ["d1"]


def test_rejected_pair_frees_nothing_it_did_not_claim():
    a1, a2 = product("m1", "x", "B"), product("m2", "x", "B")
    b1 = product("d1", "x", "B")
    candidates = [(0.9, a1, b1, frozenset()), (0.8, a2, b1, frozenset())]

    kept = resolve_conflicts(candidates, lambda score, x, y: score < 0.85)
    assert [(k[1]["sku"], k[2]["sku"]) for k in kept] == [("m2", "d1")]


def test_weight_range_is_not_read_as_pack_size():
    """"pañales de 10-15 kg" states the baby's weight, not what is in the packet."""
    assert parse_size("Pañales bebé talla 4 Deliplus de 10-15 kg") is None
    assert parse_size("Pañales 8-15 kg talla 4 Dia Planeta Bebé 44 unidades") == (44.0, "ud")


def test_unit_counts_are_parsed():
    assert parse_size("Bolsa de basura 30 L cubo alto 20 unidades") == (30.0, "L")
    assert parse_size("Bolsas de basura 20 unidades") == (20.0, "ud")
    assert parse_size("Cápsulas de café 10 cápsulas") == (10.0, "ud")


def test_record_size_reads_unit_counts_from_fields():
    record = product("m1", "Bolsas de basura", "Bosque Verde", fmt="ud",
                     unit_size=20.0, size_format="ud")
    assert record_size(record) == (20.0, "ud")


def test_nappies_of_different_counts_no_longer_pair():
    mercadona = [product("m1", "Pañales bebé talla 4 Deliplus de 10-15 kg", "Deliplus",
                         fmt="ud", unit_size=30.0, size_format="ud")]
    dia = [product("d1", "Pañales 8-15 kg talla 4 Dia Planeta Bebé 44 unidades", "Dia",
                   fmt="ud")]
    assert own_brand_candidates(mercadona, dia) == []


def priced(sku, name, brand, unit_price, reference_price, fmt, **kw):
    record = product(sku, name, brand, fmt=fmt, **kw)
    record["unit_price"] = unit_price
    record["reference_price"] = reference_price
    return record


def test_size_comes_from_the_price_ratio_not_the_declared_field():
    """Mercadona sells an 18-candle pack as `unit_size: 1.0 ud`, hiding the count."""
    pack = priced("m1", "Vela perfumada Neroli Bosque Verde", "Bosque Verde",
                  1.85, 0.103, "ud", unit_size=1.0, size_format="ud")
    assert record_size(pack) == (17.961, "ud")


def test_an_eighteen_pack_no_longer_matches_a_single_unit():
    """The declared sizes both said "1 ud", so an 18-pack matched one candle."""
    pack = priced("m1", "Vela perfumada Neroli Bosque Verde", "Bosque Verde",
                  1.85, 0.103, "ud", unit_size=1.0, size_format="ud")
    single = priced("d1", "Vela perfumada mimosa Dia Imaqe 1 unidad", "Dia Imaqe",
                    1.89, 1.89, "ud")
    assert own_brand_candidates([pack], [single]) == []


def test_declared_fields_still_used_without_a_reference_price():
    record = product("m1", "Leche", "Marca", unit_size=6.0, size_format="l")
    assert record_size(record) == (6.0, "L")


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ((4.0, "L"), (4.0, "L"), 0.0),
        ((4.0, "L"), (3.96, "L"), 0.01),
        ((4.0, "L"), (4.0, "kg"), 1.0),
        (None, (1.0, "L"), 1.0),
    ],
)
def test_size_gap(a, b, expected):
    assert size_gap(a, b) == pytest.approx(expected, abs=1e-3)


def test_an_exact_size_beats_one_merely_within_tolerance():
    """Two Mercadona rows share a name; the 3.96 L pack must win Dia's 3.96 L."""
    four_litres = priced("m4", "Refresco Coca-Cola zero", "Coca-Cola", 3.80, 0.95, "L")
    cans = priced("m396", "Refresco Coca-Cola zero", "Coca-Cola", 11.16, 2.819, "L")
    dia_cans = priced("d1", "Coca-Cola zero 12 x 330 ml", "Coca-Cola", 9.96, 2.52, "L")

    matches = match_records([four_litres, cans], [dia_cans], min_score=0.4)
    assert [m.sku_a for m in matches] == ["m396"]


def test_is_multipack_from_the_flag_or_the_title():
    assert is_multipack({"is_pack": True, "display_name": "Aceitunas"})
    assert is_multipack({"display_name": "Aceitunas rellenas Dia 3 x 50 g"})
    assert not is_multipack({"is_pack": False, "display_name": "Aceitunas Dia 150 g"})


def test_a_multipack_prefers_the_other_multipack():
    """Both chains list a single jar and a three-pack under near-identical names.

    Nothing but pack-ness separates them — and price must not, or "cheaper at X"
    stops being falsifiable. Crossed, these two pairs invented a +71% and a -42%.
    """
    single = priced("m1", "Aceitunas verdes rellenas de anchoa Hacendado", "Hacendado",
                    1.05, 7.0, "kg")
    single["is_pack"] = False
    pack = priced("m2", "Aceitunas verdes rellenas de anchoa Hacendado", "Hacendado",
                  1.80, 12.0, "kg")
    pack["is_pack"] = True
    dia_pack = priced("d1", "Aceitunas rellenas de anchoa Dia Vegecampo 3 x 50 g",
                      "Dia Vegecampo", 1.80, 12.0, "kg")
    dia_single = priced("d2", "Aceitunas rellenas de anchoa Dia Vegecampo 150 g",
                        "Dia Vegecampo", 1.05, 7.0, "kg")

    candidates = own_brand_candidates([single, pack], [dia_pack, dia_single])
    paired = {
        a["sku"]: b["sku"]
        for _, a, b, _ in resolve_conflicts(candidates, lambda *_: True)
    }
    assert paired["m2"] == "d1"  # pack with pack
    assert paired["m1"] == "d2"  # single with single


def test_an_override_also_binds_own_brand_matching(tmp_path):
    """Own brands are most of the catalogue; an override that skipped them would
    look obeyed and quietly not be."""
    path = tmp_path / "overrides.jsonl"
    path.write_text(
        '{"verdict": "different", "a": {"sku": "m1"}, "b": {"sku": "d1"}}\n', encoding="utf-8"
    )
    a = priced("m1", "Bolsa de rafia", "", 0.65, 0.65, "ud")
    b = priced("d1", "Bolsa de rafia isotérmica Dia", "Dia", 1.90, 1.90, "ud")

    assert own_brand_candidates([a], [b]) != []
    _, forbidden = load_overrides(path)
    assert own_brand_candidates([a], [b], forbidden=forbidden) == []
