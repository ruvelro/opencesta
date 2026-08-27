from __future__ import annotations

from typing import Any

import httpx

from opencesta import USER_AGENT

BASE_URL = "https://servicios.ine.es/wstempus/js/ES"

# INE's IPC series for "Alimentos y bebidas no alcohólicas" (ECOICOP group 01),
# from table 79181. This is the official yardstick our basket is measured against.
SERIES = {
    "index": "IPC290755",  # Índice
    "monthly": "IPC290756",  # Variación mensual
    "annual": "IPC290754",  # Variación anual
}


def fetch_series(series: str = "annual", last: int = 24) -> list[dict[str, Any]]:
    """Fetch the last `last` observations of an INE food-price series.

    Returns rows of {"period": "YYYY-MM", "value": float}, oldest first.
    """
    code = SERIES.get(series, series)
    resp = httpx.get(
        f"{BASE_URL}/DATOS_SERIE/{code}",
        params={"nult": last},
        headers={"User-Agent": USER_AGENT},
        timeout=30,
        follow_redirects=True,  # INE 301s the bare path
    )
    resp.raise_for_status()
    payload = resp.json()
    return [
        {"period": f"{row['Anyo']}-{row['FK_Periodo']:02d}", "value": float(row["Valor"])}
        for row in payload["Data"]
    ]


def span_series(days: int) -> tuple[str, int] | None:
    """Pick the INE series whose span our history can honestly be compared against.

    Returns (series key, tolerance days) or None when the history is too short.
    A one-week basket move set against an annual IPC figure is the easiest way
    to publish a misleading headline, so this refuses rather than approximates.
    """
    if 24 <= days <= 38:
        return ("monthly", 7)
    if 350 <= days <= 380:
        return ("annual", 15)
    return None


def compare(basket_pct: float, ine_pct: float) -> dict[str, Any]:
    """Frame our measured basket change against the official food IPC.

    `basket_pct` and `ine_pct` must cover the same span, and the caller is
    responsible for that: comparing a one-week basket move against an annual
    IPC figure would be the easiest way to publish a misleading headline.
    """
    gap = round(basket_pct - ine_pct, 2)
    return {
        "basket_pct": basket_pct,
        "ine_pct": ine_pct,
        "gap_pct": gap,
        "verdict": "por encima del IPC" if gap > 0 else "por debajo del IPC"
        if gap < 0
        else "en línea con el IPC",
    }
