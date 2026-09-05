"""Repairing published Parquet must change types and nothing else."""

import polars as pl
import pytest

from opencesta.models import SCHEMA, PriceRecord
from opencesta.retype import retype_file, retype_tree

BROKEN = {
    "chain": ["mercadona"], "zone": ["vlc1"], "sku": ["1"],
    "display_name": ["Leche"], "category": ["c"], "subcategory": ["s"],
    "unit_price": [1.5], "reference_price": [1.5], "reference_format": ["L"],
    "unit_size": [1.0], "size_format": ["l"], "tax_pct": [4.0],
    "is_pack": [False], "is_discounted": [False], "url": [""],
    "captured_at": ["2026-08-30"],
    # These are what an early snapshot got wrong: all-null, so typed Null.
    "brand": [None], "ean": [None], "origin": [None], "thumbnail": [None],
}


def write_broken(path, **overrides):
    data = {**BROKEN, **overrides}
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(data).write_parquet(path)
    return path


def test_null_columns_get_their_declared_type(tmp_path):
    path = write_broken(tmp_path / "prices.parquet")
    assert pl.read_parquet(path).schema["brand"] == pl.Null  # precondition

    fixed = retype_file(path)

    # Four Null-typed columns cast, plus the column the schema gained later.
    assert sorted(fixed) == ["brand", "captured_at_ts", "ean", "origin", "thumbnail"]
    schema = pl.read_parquet(path).schema
    assert schema["brand"] == pl.String
    assert pl.Null not in set(schema.values())


def test_values_and_row_count_are_preserved(tmp_path):
    path = write_broken(tmp_path / "prices.parquet")
    before = pl.read_parquet(path)

    retype_file(path)
    after = pl.read_parquet(path)

    assert after.height == before.height
    assert after["unit_price"].to_list() == before["unit_price"].to_list()
    assert after["sku"].to_list() == before["sku"].to_list()
    assert after["brand"].to_list() == [None]  # still null, just typed
    assert after.columns == PriceRecord.columns()


def test_a_healthy_file_is_left_alone(tmp_path):
    path = tmp_path / "prices.parquet"
    healthy = {**BROKEN, "captured_at_ts": ["2026-09-05T09:07:00+00:00"]}
    pl.DataFrame(healthy, schema=SCHEMA).write_parquet(path)
    stamp = path.stat().st_mtime_ns

    assert retype_file(path) == []
    assert path.stat().st_mtime_ns == stamp  # not rewritten


def test_an_unknown_null_column_is_refused(tmp_path):
    """Better to stop than to silently drop a column the schema does not know."""
    path = tmp_path / "prices.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pl.DataFrame({**BROKEN, "columna_rara": [None]}).write_parquet(path)

    with pytest.raises(ValueError, match="fuera del esquema"):
        retype_file(path)


def test_repaired_files_read_in_a_single_pass(tmp_path):
    """The point of the repair: the history becomes one readable series again."""
    write_broken(tmp_path / "date=2026-08-30" / "prices.parquet")
    write_broken(tmp_path / "date=2026-08-29" / "prices.parquet",
                 captured_at=["2026-08-29"], size_format=[None], unit_size=[None])

    with pytest.raises(pl.exceptions.SchemaError):
        pl.read_parquet(tmp_path / "**" / "*.parquet")

    assert len(retype_tree(tmp_path)) == 2
    combined = pl.read_parquet(tmp_path / "**" / "*.parquet")
    assert combined.height == 2
    assert sorted(combined["captured_at"].to_list()) == ["2026-08-29", "2026-08-30"]


def test_a_column_the_schema_gained_later_is_added_as_typed_null(tmp_path):
    """Files written before captured_at_ts existed must still read with new ones."""
    old = write_broken(tmp_path / "old" / "prices.parquet")
    frame = pl.read_parquet(old).drop("brand", "ean", "origin", "thumbnail")
    frame.write_parquet(old)  # now healthy types but missing four columns + the new one
    assert "captured_at_ts" not in pl.read_parquet(old).columns

    fixed = retype_file(old)

    assert "captured_at_ts" in fixed
    repaired = pl.read_parquet(old)
    assert repaired.columns == PriceRecord.columns()
    assert repaired.schema["captured_at_ts"] == pl.String
    assert repaired["captured_at_ts"].to_list() == [None]


def test_old_and_new_files_read_together_after_retype(tmp_path):
    write_broken(tmp_path / "date=2026-08-30" / "prices.parquet")
    new = tmp_path / "date=2026-09-06" / "prices.parquet"
    new.parent.mkdir(parents=True)
    pl.DataFrame({**BROKEN, "captured_at": ["2026-09-06"],
                  "captured_at_ts": ["2026-09-06T09:07:00+00:00"]},
                 schema=SCHEMA).write_parquet(new)

    with pytest.raises(pl.exceptions.SchemaError):
        pl.read_parquet(tmp_path / "**" / "*.parquet")
    retype_tree(tmp_path)
    combined = pl.read_parquet(tmp_path / "**" / "*.parquet")
    assert combined.height == 2
    assert combined["captured_at_ts"].null_count() == 1
