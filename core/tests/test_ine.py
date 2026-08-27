import httpx
import pytest

from opencesta import ine


def test_fetch_series_shapes_rows(monkeypatch):
    payload = {
        "COD": "IPC290754",
        "Nombre": "Nacional. Alimentos y bebidas no alcohólicas. Variación anual.",
        "Data": [
            {"Anyo": 2026, "FK_Periodo": 6, "Valor": 1.9},
            {"Anyo": 2026, "FK_Periodo": 7, "Valor": 1.6},
        ],
    }
    seen = {}

    def fake_get(url, **kwargs):
        seen["url"] = url
        seen["params"] = kwargs.get("params")
        seen["follow_redirects"] = kwargs.get("follow_redirects")
        return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(ine.httpx, "get", fake_get)
    rows = ine.fetch_series("annual", last=2)

    assert rows == [
        {"period": "2026-06", "value": 1.9},
        {"period": "2026-07", "value": 1.6},
    ]
    assert seen["url"].endswith("/DATOS_SERIE/IPC290754")
    assert seen["params"] == {"nult": 2}
    # INE 301s the bare path; without this the request silently returns HTML.
    assert seen["follow_redirects"] is True


def test_fetch_series_accepts_raw_code(monkeypatch):
    monkeypatch.setattr(
        ine.httpx,
        "get",
        lambda url, **kw: httpx.Response(
            200, json={"Data": []}, request=httpx.Request("GET", url)
        ),
    )
    assert ine.fetch_series("IPC290755") == []


@pytest.mark.parametrize(
    ("basket", "official", "gap", "verdict"),
    [
        (9.0, 3.1, 5.9, "por encima del IPC"),
        (1.0, 3.1, -2.1, "por debajo del IPC"),
        (3.1, 3.1, 0.0, "en línea con el IPC"),
    ],
)
def test_compare(basket, official, gap, verdict):
    result = ine.compare(basket, official)
    assert result["gap_pct"] == gap
    assert result["verdict"] == verdict
