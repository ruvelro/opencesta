import json

from opencesta.review import build_rows, is_suspect, write_review


def equivalence(sku_a="m1", sku_b="d1", method="brand-size-name", score=1.0, size=1.0,
                terms=None):
    return {
        "method": method,
        "brand": "Marca",
        "reference_format": "L",
        "size": size,
        "score": score,
        "shared_terms": terms if terms is not None else ["leche"],
        "a": {"sku": sku_a, "name": "Leche Marca"},
        "b": {"sku": sku_b, "name": "Leche Marca 1 L"},
    }


def priced(sku, price):
    return {sku: {"sku": sku, "reference_price": price}}


def test_gap_is_dia_relative_to_mercadona():
    rows = build_rows([equivalence()], priced("m1", 2.0), priced("d1", 2.5))
    assert rows[0]["gap"] == 25.0  # Dia is 25% dearer


def test_missing_price_leaves_the_gap_unknown_rather_than_zero():
    rows = build_rows([equivalence()], priced("m1", 2.0), priced("d1", None))
    assert rows[0]["gap"] is None


def test_missing_product_does_not_crash():
    rows = build_rows([equivalence()], {}, {})
    assert rows[0]["gap"] is None
    assert rows[0]["a"]["price"] is None


def test_judged_rows_show_the_reason_not_the_shared_terms():
    rows = build_rows(
        [equivalence(method="own-brand-substitute", terms=["Ambas son leche entera."])],
        priced("m1", 1.0), priced("d1", 1.0),
    )
    assert rows[0]["why"] == "Ambas son leche entera."


def test_scored_rows_join_the_shared_terms():
    rows = build_rows(
        [equivalence(method="own-brand-score", terms=["leche", "entera"])],
        priced("m1", 1.0), priced("d1", 1.0),
    )
    assert rows[0]["why"] == "leche, entera"


def test_a_wide_price_gap_is_suspect():
    """Chains price identical goods within a few percent; a huge gap smells of a bad match."""
    assert is_suspect({"gap": 40.0, "size": 1.0, "score": 1.0})
    assert not is_suspect({"gap": 3.0, "size": 1.0, "score": 1.0})


def test_no_size_or_a_weak_score_is_suspect():
    assert is_suspect({"gap": 0.0, "size": None, "score": 1.0})
    assert is_suspect({"gap": 0.0, "size": 1.0, "score": 0.42})


def test_page_is_self_contained_and_embeds_its_data(tmp_path):
    rows = build_rows([equivalence()], priced("m1", 2.0), priced("d1", 2.5))
    path = write_review(rows, tmp_path / "review.html", "mercadona vs dia")
    page = path.read_text(encoding="utf-8")

    assert "Leche Marca 1 L" in page
    assert "mercadona vs dia" in page
    # No external requests: the page must open from disk with no network.
    assert "http://" not in page and "https://" not in page
    assert "__DATA__" not in page and "__STATS__" not in page


def test_embedded_data_is_valid_json(tmp_path):
    rows = build_rows([equivalence()], priced("m1", 2.0), priced("d1", 2.5))
    page = write_review(rows, tmp_path / "r.html", "x").read_text(encoding="utf-8")

    start = page.index("const DATA = ") + len("const DATA = ")
    payload = json.loads(page[start : page.index(";\n", start)])
    assert payload[0]["a"]["name"] == "Leche Marca"


def test_subtitle_is_escaped(tmp_path):
    page = write_review([], tmp_path / "r.html", "<script>alert(1)</script>").read_text(
        encoding="utf-8"
    )
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;" in page
