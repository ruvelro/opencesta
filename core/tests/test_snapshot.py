"""The published dataset must be readable in one pass across every chain.

Before the schema was declared, each snapshot inferred its own dtypes, so a
column a chain never fills (Dia's `size_format`, Mercadona's listing `ean`) was
written as a Null column. Reading the release with a single glob then failed
with a SchemaError — breaking the one promise the dataset makes.
"""

import dataclasses

import polars as pl
import pytest

from opencesta import snapshot as snapshot_module
from opencesta.models import SCHEMA, PriceRecord


def record(chain, zone, **overrides):
    base = {
        "chain": chain, "zone": zone, "sku": "1", "display_name": "Producto",
        "category": "c", "subcategory": "s", "unit_price": 1.0,
        "reference_price": 1.0, "reference_format": "L", "unit_size": 1.0,
        "size_format": "l", "tax_pct": 4.0, "is_pack": False,
        "is_discounted": False, "url": "", "captured_at": "2026-08-31",
        "brand": "Marca", "ean": "123", "origin": "España", "thumbnail": None,
    }
    return PriceRecord(**{**base, **overrides})


def write(tmp_path, chain, zone, records, monkeypatch):
    class FakeAdapter:
        def __init__(self):
            pass

        def iter_products(self, zone_, captured_at):
            return iter(records)

    monkeypatch.setitem(snapshot_module.ADAPTERS, chain, FakeAdapter)
    return snapshot_module.snapshot(chain, zone, tmp_path, captured_at="2026-08-31")


def test_schema_covers_exactly_the_record_columns():
    assert list(SCHEMA) == PriceRecord.columns()


def test_all_null_columns_keep_their_declared_type(tmp_path, monkeypatch):
    """Dia fills no size_format at all; it must still be a String column."""
    dia = [record("dia", "es-default", size_format=None, unit_size=None, ean=None)]
    path = write(tmp_path, "dia", "es-default", dia, monkeypatch)

    schema = pl.read_parquet(path).schema
    assert schema["size_format"] == pl.String
    assert schema["unit_size"] == pl.Float64
    assert schema["ean"] == pl.String
    assert pl.Null not in set(schema.values())


def test_two_chains_read_in_a_single_pass(tmp_path, monkeypatch):
    write(tmp_path, "mercadona", "vlc1",
          [record("mercadona", "vlc1", brand=None, ean=None, origin=None)], monkeypatch)
    write(tmp_path, "dia", "es-default",
          [record("dia", "es-default", size_format=None, unit_size=None,
                  thumbnail=None, tax_pct=None)], monkeypatch)

    combined = pl.read_parquet(tmp_path / "**" / "*.parquet")
    assert combined.shape[0] == 2
    assert set(combined["chain"].to_list()) == {"mercadona", "dia"}


def test_a_snapshot_with_no_products_is_an_error(tmp_path, monkeypatch):
    """Publishing an empty zone would look like the chain dropped its catalog."""
    with pytest.raises(RuntimeError, match="0 products"):
        write(tmp_path, "mercadona", "vlc1", [], monkeypatch)


def test_column_order_is_stable(tmp_path, monkeypatch):
    path = write(tmp_path, "mercadona", "vlc1", [record("mercadona", "vlc1")], monkeypatch)
    assert pl.read_parquet(path).columns == [f.name for f in dataclasses.fields(PriceRecord)]
