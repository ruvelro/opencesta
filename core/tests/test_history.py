import dataclasses

import polars as pl
import pytest

from opencesta.history import available_dates, diff
from opencesta.models import PriceRecord


def write_snapshot(tmp_path, date, products):
    records = [
        dataclasses.asdict(
            PriceRecord(
                chain="mercadona", zone="vlc1", sku=sku, display_name=f"p{sku}",
                category="c", subcategory="s", unit_price=price, reference_price=price,
                reference_format="L", unit_size=1.0, size_format="l", tax_pct=4.0,
                is_pack=False, is_discounted=False, url="", captured_at=date,
            )
        )
        for sku, price in products.items()
    ]
    target = tmp_path / "chain=mercadona" / "zone=vlc1" / f"date={date}"
    target.mkdir(parents=True)
    pl.DataFrame(records).select(PriceRecord.columns()).write_parquet(target / "prices.parquet")


@pytest.fixture
def two_snapshots(tmp_path):
    write_snapshot(tmp_path, "2026-08-20", {"1": 10.0, "2": 4.0, "gone": 3.0})
    write_snapshot(tmp_path, "2026-08-27", {"1": 12.0, "2": 2.0, "new": 5.0})
    return tmp_path


def test_available_dates(two_snapshots):
    assert available_dates(two_snapshots, "mercadona", "vlc1") == ["2026-08-20", "2026-08-27"]


def test_diff_classifies_moves(two_snapshots):
    r = diff(two_snapshots, "mercadona", "vlc1")
    assert (r["since"], r["until"]) == ("2026-08-20", "2026-08-27")
    assert r["tracked"] == 2
    assert r["added"]["sku"].to_list() == ["new"]
    assert r["removed"]["sku"].to_list() == ["gone"]

    changed = r["changed"]
    assert changed["sku"].to_list() == ["2", "1"]  # sorted by pct, biggest drop first
    assert changed["delta"].to_list() == [-2.0, 2.0]
    assert changed["pct"].to_list() == [-50.0, 20.0]


def test_diff_basket_pct(two_snapshots):
    # comparable catalog: 14.00 -> 14.00 == flat, despite both items moving
    assert diff(two_snapshots, "mercadona", "vlc1")["basket_pct"] == 0.0


def test_diff_needs_two_snapshots(tmp_path):
    write_snapshot(tmp_path, "2026-08-27", {"1": 1.0})
    with pytest.raises(ValueError, match="need 2\\+ snapshots"):
        diff(tmp_path, "mercadona", "vlc1")


def test_diff_rejects_unknown_date(two_snapshots):
    with pytest.raises(ValueError, match="since='1999-01-01'"):
        diff(two_snapshots, "mercadona", "vlc1", since="1999-01-01")
