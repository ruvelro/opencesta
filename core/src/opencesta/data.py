"""Fetch and cache the published dataset, so nothing needs cloning to use it.

The daily pipeline publishes one release per day with the prices, the
cross-chain equivalences and the catalog. This pulls the most recent N of them
into a local cache, keyed by release tag, so a second call fetches nothing it
already has. Point OPENCESTA_DATA at a directory to skip GitHub entirely.
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path
from typing import Any

import httpx

from opencesta import USER_AGENT

REPO = "ruvelro/opencesta"
API = f"https://api.github.com/repos/{REPO}/releases"

PRICES = "prices.tar.gz"
EQUIVALENCES = "equivalences.jsonl"
CATALOG = "catalog-mercadona.parquet"


def default_cache_dir() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "opencesta"


def list_releases(client: httpx.Client, days: int) -> list[dict[str, Any]]:
    """The newest `days` releases, each as {tag, assets: {name: url}}."""
    resp = client.get(API, params={"per_page": days}, headers={"User-Agent": USER_AGENT})
    resp.raise_for_status()
    releases = []
    for release in resp.json():
        if release.get("draft") or not release["tag_name"].startswith("prices-"):
            continue
        assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
        releases.append({"tag": release["tag_name"], "assets": assets})
    return releases


def _download(client: httpx.Client, url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", url, headers={"User-Agent": USER_AGENT},
                       follow_redirects=True) as resp:
        resp.raise_for_status()
        with target.open("wb") as handle:
            for chunk in resp.iter_bytes():
                handle.write(chunk)


def ensure_data(
    days: int = 2,
    cache_dir: Path | None = None,
    client: httpx.Client | None = None,
) -> Path:
    """Return a directory laid out like the repo's, holding the last `days` releases.

    Layout: <dir>/data/chain=…/zone=…/date=…/prices.parquet,
            <dir>/data/catalog/mercadona.parquet, <dir>/equivalences.jsonl.

    With OPENCESTA_DATA set, that directory is returned as-is and nothing is
    fetched — the way to work offline or on a local snapshot.
    """
    local = os.environ.get("OPENCESTA_DATA")
    if local:
        return Path(local)

    cache = cache_dir or default_cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    client = client or httpx.Client(timeout=60)
    releases = list_releases(client, days)
    if not releases:
        raise RuntimeError("no hay releases publicadas")

    tags_dir = cache / "releases"
    for index, release in enumerate(releases):
        marker = tags_dir / release["tag"]
        if marker.exists():
            continue
        assets = release["assets"]
        if PRICES not in assets:
            continue
        archive = cache / f"{release['tag']}.tar.gz"
        _download(client, assets[PRICES], archive)
        with tarfile.open(archive) as tar:
            tar.extractall(cache, filter="data")
        archive.unlink()
        # Equivalences and catalog are cumulative: the newest release wins.
        if index == 0:
            if EQUIVALENCES in assets:
                _download(client, assets[EQUIVALENCES], cache / EQUIVALENCES)
            if CATALOG in assets:
                _download(client, assets[CATALOG], cache / "data" / "catalog" / "mercadona.parquet")
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    return cache
