import io
import tarfile

import httpx
import polars as pl
import pytest

from opencesta import data as data_module
from opencesta.data import CATALOG, EQUIVALENCES, PRICES, ensure_data, list_releases
from opencesta.models import SCHEMA


def make_tarball(date: str) -> bytes:
    """A prices.tar.gz with one tiny parquet, laid out like a real release."""
    row = {k: [v] for k, v in {
        "chain": "mercadona", "zone": "vlc1", "sku": "1", "display_name": "Leche",
        "category": "c", "subcategory": "s", "unit_price": 1.0, "reference_price": 1.0,
        "reference_format": "L", "unit_size": 1.0, "size_format": "l", "tax_pct": 4.0,
        "is_pack": False, "is_discounted": False, "url": "", "captured_at": date,
        "brand": None, "ean": None, "origin": None, "thumbnail": None,
        "captured_at_ts": None,
    }.items()}
    parquet = io.BytesIO()
    pl.DataFrame(row, schema=SCHEMA).write_parquet(parquet)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo(f"data/chain=mercadona/zone=vlc1/date={date}/prices.parquet")
        info.size = len(parquet.getvalue())
        tar.addfile(info, io.BytesIO(parquet.getvalue()))
    return buf.getvalue()


def fake_github(dates: list[str], hits: list[str]):
    """Serve a releases listing and the assets for each date, recording downloads."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith(data_module.API):
            assert request.headers["User-Agent"].startswith("OpenCesta/")
            releases = [{
                "tag_name": f"prices-{d}", "draft": False,
                "assets": [{"name": n, "browser_download_url": f"https://dl.test/{d}/{n}"}
                           for n in (PRICES, EQUIVALENCES, CATALOG)],
            } for d in dates]
            return httpx.Response(200, json=releases)
        hits.append(url)
        date, name = url.rsplit("/", 2)[1:]
        if name == PRICES:
            return httpx.Response(200, content=make_tarball(date))
        if name == EQUIVALENCES:
            return httpx.Response(200, content=b'{"a":{"sku":"1"},"b":{"sku":"2"},"method":"x"}\n')
        return httpx.Response(200, content=b"parquet-bytes")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_list_releases_skips_drafts_and_foreign_tags():
    def handler(request):
        return httpx.Response(200, json=[
            {"tag_name": "prices-2026-09-05", "draft": False, "assets": []},
            {"tag_name": "prices-2026-09-04", "draft": True, "assets": []},
            {"tag_name": "v0.1.0", "draft": False, "assets": []},
        ])
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert [r["tag"] for r in list_releases(client, 5)] == ["prices-2026-09-05"]


def test_ensure_data_fetches_extracts_and_lays_out(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCESTA_DATA", raising=False)
    hits: list[str] = []
    root = ensure_data(days=2, cache_dir=tmp_path, client=fake_github(
        ["2026-09-05", "2026-09-04"], hits))

    assert root == tmp_path
    assert (root / "data/chain=mercadona/zone=vlc1/date=2026-09-05/prices.parquet").exists()
    assert (root / "data/chain=mercadona/zone=vlc1/date=2026-09-04/prices.parquet").exists()
    assert (root / "equivalences.jsonl").read_text().startswith('{"a"')
    assert (root / "data/catalog/mercadona.parquet").read_bytes() == b"parquet-bytes"
    # Prices for both days; equivalences and catalog only from the newest.
    assert sum(u.endswith(PRICES) for u in hits) == 2
    assert sum(u.endswith(EQUIVALENCES) for u in hits) == 1
    assert not list(root.glob("*.tar.gz"))  # archives are removed after extraction


def test_ensure_data_is_incremental(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCESTA_DATA", raising=False)
    hits: list[str] = []
    ensure_data(days=1, cache_dir=tmp_path, client=fake_github(["2026-09-04"], hits))
    assert len(hits) == 3

    hits.clear()
    ensure_data(days=2, cache_dir=tmp_path, client=fake_github(["2026-09-05", "2026-09-04"], hits))
    assert sum(u.endswith(PRICES) for u in hits) == 1  # only the new day
    assert not any("2026-09-04" in u for u in hits)


def test_ensure_data_honours_a_local_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCESTA_DATA", str(tmp_path / "mine"))

    def explode(request):
        raise AssertionError("no debería tocar la red")

    root = ensure_data(client=httpx.Client(transport=httpx.MockTransport(explode)))
    assert root == tmp_path / "mine"


def test_no_releases_is_an_error(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENCESTA_DATA", raising=False)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    with pytest.raises(RuntimeError, match="no hay releases"):
        ensure_data(cache_dir=tmp_path, client=client)
