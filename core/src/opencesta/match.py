"""Deterministic matching of national-brand products across chains.

This is level (b) of the cascade: brand + pack size + name overlap. It is cheap,
explainable and needs no model. It only covers products whose brand exists in
both chains — own brands (Hacendado vs Dia) have no shared brand token and are
left to the later, more expensive levels.

Level (a), matching by EAN, is not implemented on purpose: Dia exposes no EAN at
all (0 of 5503 products), so there is nothing to join on.

Deliberate omission: price is never an input to matching. If similar prices made
two products more likely to be judged equivalent, "this product is cheaper at X"
would become unfalsifiable — the matcher would only ever surface pairs that
already agree. Price is what we measure, so it must stay out of the decision.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Words that carry no distinguishing meaning in a product title.
STOPWORDS = frozenset([
    "de", "del", "la", "el", "los", "las", "con", "sin", "y", "a", "en", "para", "al",
    "un", "una", "pack", "lote", "formato", "tamano", "bolsa", "caja", "botella",
    "brik", "tarrina", "bandeja", "envase", "unidad", "unidades", "ud", "uds",
    "aprox", "aproximado",
])

# "pack 6 x 1 L", "12 x 330 ml", "330 g", "1,5 L"
_MULTI_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[x×]\s*(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml|cl)\b")
# The leading guard rejects the tail of a range: "pañales de 10-15 kg" states the
# baby's weight, not the pack size, and reading it as one paired a Mercadona
# nappy with a Dia nappy of a different count purely because both said "15 kg".
_SINGLE_RE = re.compile(r"(?<![\d,.-])(\d+(?:[.,]\d+)?)\s*(kg|g|l|ml|cl)\b")
# Counted goods (nappies, bin bags, capsules) state a unit count instead.
_COUNT_RE = re.compile(r"(?<![\d,.-])(\d+)\s*(?:unidades|unidad|uds|ud|c[aá]psulas)\b")

# A number standing on its own: "talla 4", "nº 6.6", "85%", "20 unidades". The
# guards exclude the halves of a range, so the "4" in "4-8 kg" is not a lone 4.
_STANDALONE_NUMBER = re.compile(r"(?<![\d,.\-])(\d+(?:[.,]\d+)?)(?![\d,.\-]?\d)")

# Everything normalized to kilograms or litres so the two chains are comparable.
_TO_BASE = {"kg": 1.0, "g": 0.001, "l": 1.0, "ml": 0.001, "cl": 0.01}
_BASE_UNIT = {"kg": "kg", "g": "kg", "l": "L", "ml": "L", "cl": "L"}


def strip_accents(text: str) -> str:
    return "".join(
        c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn"
    )


def tokenize(text: str, brand: str | None = None) -> frozenset[str]:
    """Significant words of a title, with the brand and any size text removed.

    The brand is dropped because every candidate pair already shares it, so it
    would inflate every score equally without separating anything.
    """
    cleaned = strip_accents(text.lower())
    cleaned = _MULTI_RE.sub(" ", cleaned)
    cleaned = _SINGLE_RE.sub(" ", cleaned)
    brand_tokens = set()
    if brand:
        brand_tokens = {t for t in re.split(r"[^a-z0-9]+", strip_accents(brand.lower())) if t}
    words = re.split(r"[^a-z0-9]+", cleaned)
    return frozenset(
        w for w in words if len(w) > 2 and w not in STOPWORDS and w not in brand_tokens
    )


def standalone_numbers(text: str) -> frozenset[str]:
    """Numbers that identify a variant — "talla 4", "72%", "nº 6.6".

    Size expressions are stripped first: "400 g", "58 unidades", "6 x 1 L" are
    quantities the size logic already compares, not variant markers, and
    treating them as such made "Pizza 4 quesos" conflict with "Pizza cuatro
    quesos 400 g".
    """
    cleaned = strip_accents(text.lower())
    for pattern in (_MULTI_RE, _SINGLE_RE, _COUNT_RE):
        cleaned = pattern.sub(" ", cleaned)
    return frozenset(n.replace(",", ".") for n in _STANDALONE_NUMBER.findall(cleaned))


def numbers_conflict(name_a: str, name_b: str) -> bool:
    """Both names state numbers and share none of them.

    Word tokens drop single digits, so "talla 4" and "talla 2" looked identical
    and Dodot size 4 was paired with Dodot size 2 — the packs happened to hold
    the same count. A name with no numbers never conflicts: we cannot tell.
    """
    a, b = standalone_numbers(name_a), standalone_numbers(name_b)
    return bool(a) and bool(b) and not (a & b)


def parse_size(text: str) -> tuple[float, str] | None:
    """Total quantity stated in a title, normalized to kg or L.

    "pack 6 x 1 L" -> (6.0, "L");  "12 x 330 ml" -> (3.96, "L");  "330 g" -> (0.33, "kg")
    """
    cleaned = strip_accents(text.lower())
    multi = _MULTI_RE.search(cleaned)
    if multi:
        count = float(multi.group(1).replace(",", "."))
        each = float(multi.group(2).replace(",", "."))
        unit = multi.group(3)
        return round(count * each * _TO_BASE[unit], 4), _BASE_UNIT[unit]
    single = _SINGLE_RE.search(cleaned)
    if single:
        amount = float(single.group(1).replace(",", "."))
        unit = single.group(2)
        return round(amount * _TO_BASE[unit], 4), _BASE_UNIT[unit]
    count = _COUNT_RE.search(cleaned)
    if count:
        return float(count.group(1)), "ud"
    return None


def record_size(record: dict[str, Any]) -> tuple[float, str] | None:
    """How much of the product you get, in the units its reference price uses.

    Derived from `unit_price / reference_price` whenever both are present. That
    ratio is the size the price is actually quoted against, which is the only
    size two chains can be compared on — and it is right where the declared
    fields are not. Mercadona sells an 18-candle pack as `unit_size: 1.0 ud`
    with the count hidden in a field we do not capture, so the declared size
    said "1 unit" and the pack matched Dia's single candle, 18x its price.

    Falls back to the declared fields, then to the title, when a record carries
    no usable reference price.
    """
    unit_price = record.get("unit_price")
    reference_price = record.get("reference_price")
    reference_format = record.get("reference_format")
    if unit_price and reference_price and reference_format:
        return round(unit_price / reference_price, 3), reference_format

    size, fmt = record.get("unit_size"), record.get("size_format")
    if size and fmt:
        key = fmt.lower()
        if key in _TO_BASE:
            return round(float(size) * _TO_BASE[key], 4), _BASE_UNIT[key]
        if key in ("ud", "uds", "unidad", "unidades"):
            return float(size), "ud"
    return parse_size(record.get("display_name", ""))


@dataclass(frozen=True, slots=True)
class Equivalence:
    brand: str
    sku_a: str
    name_a: str
    sku_b: str
    name_b: str
    reference_format: str
    size: float | None
    score: float
    shared_terms: tuple[str, ...]
    method: str = "brand-size-name"

    def as_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "brand": self.brand,
            "reference_format": self.reference_format,
            "size": self.size,
            "score": self.score,
            "shared_terms": list(self.shared_terms),
            "a": {"sku": self.sku_a, "name": self.name_a},
            "b": {"sku": self.sku_b, "name": self.name_b},
        }


# How each chain marks its own brand. Mercadona's own products carry its internal
# GS1 prefixes; Dia names every own line "Dia <something>" (Dia Láctea, Diasol...).
# Products with no brand at all are treated as own brand: in both chains those are
# unbranded staples (fruit, bakery), which is exactly what we want to compare.
_OWN_BRAND_EAN_PREFIXES = {"mercadona": ("8480000", "8402001")}
_OWN_BRAND_NAME_PREFIXES = {"dia": ("dia",)}


def is_own_brand(chain: str, record: dict[str, Any]) -> bool:
    ean = record.get("ean") or ""
    if any(ean.startswith(p) for p in _OWN_BRAND_EAN_PREFIXES.get(chain, ())):
        return True
    brand = (record.get("brand") or "").strip().lower()
    if not brand:
        return True
    return any(brand.startswith(p) for p in _OWN_BRAND_NAME_PREFIXES.get(chain, ()))


def own_brand_candidates(
    records_a: list[dict[str, Any]],
    records_b: list[dict[str, Any]],
    min_score: float = 0.4,
    forbidden: set[tuple[str, str]] | None = None,
) -> list[tuple[float, dict, dict, frozenset[str]]]:
    """Candidate pairs for own-brand products, which share neither EAN nor brand.

    Brand equality cannot be required here, so the pack size carries the load:
    products are bucketed by (reference format, exact size) and only compared
    within a bucket. That turns 3000x2000 into a few thousand comparisons and,
    more importantly, stops a 1 L bottle from matching a 5 L drum.

    Pairs a human has marked "different" in the overrides never surface. A
    correction has to hold here too: own brands are most of the catalogue, so an
    override that only bound national brands would look obeyed and not be.

    Returns every surviving candidate sorted best-first, without resolving
    conflicts — the caller decides which ones to accept, since some need judging.
    """
    forbidden = forbidden or set()
    # Weighed and measured goods are bucketed by exact size; counted goods share
    # one bucket per format and are filtered by sizes_agree, whose wider band
    # for "ud" is what lets a 58-pack meet a 62-pack.
    def bucket_key(fmt: str, size: tuple[float, str]) -> tuple[str, float | None]:
        return (fmt, None if fmt == "ud" else round(size[0], 3))

    buckets: dict[tuple[str, float | None], list[tuple[dict, frozenset[str], Any]]] = {}
    for record in records_b:
        size = record_size(record)
        fmt = record.get("reference_format")
        if not size or not fmt:
            continue
        buckets.setdefault(bucket_key(fmt, size), []).append(
            (record, tokenize(record["display_name"], record.get("brand")), size)
        )

    candidates = []
    for record in records_a:
        size = record_size(record)
        fmt = record.get("reference_format")
        if not size or not fmt:
            continue
        tokens_a = tokenize(record["display_name"], record.get("brand"))
        for other, tokens_b, size_b in buckets.get(bucket_key(fmt, size), []):
            if not sizes_agree(size, size_b):
                continue
            if (str(record["sku"]), str(other["sku"])) in forbidden:
                continue
            if numbers_conflict(record["display_name"], other["display_name"]):
                continue
            score = score_pair(tokens_a, tokens_b)
            if score >= min_score:
                candidates.append((
                    score,
                    is_multipack(record) == is_multipack(other),
                    -size_gap(size, record_size(other)),
                    record, other, tokens_a & tokens_b,
                ))
    candidates.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    return [(score, a, b, shared) for score, _, _, a, b, shared in candidates]


def resolve_conflicts(
    candidates: list[tuple[float, dict, dict, frozenset[str]]],
    accept: Any,
) -> list[tuple[float, dict, dict, frozenset[str]]]:
    """Keep the best accepted pair per product, walking best-first.

    `accept(score, record_a, record_b)` decides; it is consulted only for pairs
    whose products are both still free, so a judge is never asked about a pair
    that a better one has already claimed.
    """
    used_a: set[str] = set()
    used_b: set[str] = set()
    kept = []
    for score, record, other, shared in candidates:
        if str(record["sku"]) in used_a or str(other["sku"]) in used_b:
            continue
        if not accept(score, record, other):
            continue
        used_a.add(str(record["sku"]))
        used_b.add(str(other["sku"]))
        kept.append((score, record, other, shared))
    return kept


def load_for_matching(
    prices_dir: Path,
    a: tuple[str, str],
    b: tuple[str, str],
    date: str | None = None,
) -> tuple[tuple[list[dict], list[dict]], str]:
    """Load one snapshot per chain for the newest date both of them have.

    Brand and EAN are taken from the enriched catalog when the chain keeps them
    there: Mercadona's category listing carries neither, so without this join
    national-brand matching finds nothing and every product looks own-brand.
    """
    import polars as pl  # local: keeps the pure-matching helpers import-light

    enriched = ["brand", "ean"]
    frames = {}
    for chain, zone in (a, b):
        frame = pl.read_parquet(prices_dir / f"chain={chain}" / f"zone={zone}" / "**" / "*.parquet")
        catalog_path = prices_dir / "catalog" / f"{chain}.parquet"
        if catalog_path.exists():
            catalog = (
                pl.read_parquet(catalog_path).select("sku", *enriched).unique(subset="sku")
            )
            frame = frame.drop(enriched).join(catalog, on="sku", how="left")
        frames[(chain, zone)] = frame

    dates = set.intersection(*(set(f["captured_at"].to_list()) for f in frames.values()))
    if not dates:
        raise ValueError("the two chains share no snapshot date")
    chosen = date or max(dates)
    if chosen not in dates:
        raise ValueError(f"date={chosen!r} not shared; available: {', '.join(sorted(dates))}")

    return (
        frames[a].filter(pl.col("captured_at") == chosen).to_dicts(),
        frames[b].filter(pl.col("captured_at") == chosen).to_dicts(),
    ), chosen


def load_overrides(path: Path) -> tuple[dict[tuple[str, str], str], set[tuple[str, str]]]:
    """Read community corrections from a JSONL file.

    Two kinds of line, both keyed by `{"a": {"sku": ...}, "b": {"sku": ...}}`:
      {"verdict": "equivalent", ...}  force this pair
      {"verdict": "different",  ...}  forbid this pair

    Human corrections are permanent and win over anything computed. This is the
    part a closed project cannot have: one person's fix helps everyone, forever.
    """
    forced: dict[tuple[str, str], str] = {}
    forbidden: set[tuple[str, str]] = set()
    if not path.exists():
        return forced, forbidden
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            row = json.loads(line)
            pair = (str(row["a"]["sku"]), str(row["b"]["sku"]))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise ValueError(f"{path}:{number}: malformed override ({exc})") from exc
        if row.get("verdict") == "equivalent":
            forced[pair] = row.get("note", "")
        elif row.get("verdict") == "different":
            forbidden.add(pair)
        else:
            raise ValueError(f"{path}:{number}: verdict must be 'equivalent' or 'different'")
    return forced, forbidden


def write_equivalences(equivalences: list[Equivalence], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for equivalence in equivalences:
            handle.write(json.dumps(equivalence.as_dict(), ensure_ascii=False) + "\n")
    return path


def score_pair(tokens_a: frozenset[str], tokens_b: frozenset[str]) -> float:
    """Jaccard overlap of the distinguishing words. 1.0 means identical wording."""
    if not tokens_a or not tokens_b:
        return 0.0
    return round(len(tokens_a & tokens_b) / len(tokens_a | tokens_b), 3)


def is_multipack(record: dict[str, Any]) -> bool:
    """Whether the product is several containers sold together.

    Mercadona flags it; Dia only says so in the title ("3 x 50 g"). It matters
    because a chain can list the single jar and the three-pack under one name,
    and then the only thing telling them apart is the price — which must not
    decide a match, or "cheaper at X" stops being falsifiable.
    """
    if record.get("is_pack"):
        return True
    return bool(_MULTI_RE.search(strip_accents((record.get("display_name") or "").lower())))


def size_gap(a: tuple[float, str] | None, b: tuple[float, str] | None) -> float:
    """Relative difference between two sizes; 0.0 when they are identical."""
    if a is None or b is None or a[1] != b[1]:
        return 1.0
    larger = max(a[0], b[0])
    return 0.0 if larger == 0 else abs(a[0] - b[0]) / larger


# Counted goods get a wider tolerance than weighed or measured ones. A 58-nappy
# pack and a 62-nappy pack are the same product to anyone buying nappies, and
# the reference price is already per unit, so the comparison stays fair. The
# band is still tight enough that an 18-candle pack never meets a single candle.
_TOLERANCE = {"ud": 0.25}
_DEFAULT_TOLERANCE = 0.02


def sizes_agree(a: tuple[float, str] | None, b: tuple[float, str] | None) -> bool:
    """Same unit and within tolerance — rounding slack, never a different pack."""
    if a is None or b is None:
        return False
    if a[1] != b[1]:
        return False
    larger = max(a[0], b[0])
    tolerance = _TOLERANCE.get(a[1], _DEFAULT_TOLERANCE)
    return larger > 0 and abs(a[0] - b[0]) / larger <= tolerance


def match_records(
    records_a: list[dict[str, Any]],
    records_b: list[dict[str, Any]],
    min_score: float = 0.5,
    require_size: bool = True,
    overrides: tuple[dict[tuple[str, str], str], set[tuple[str, str]]] | None = None,
) -> list[Equivalence]:
    """Pair up products of the same national brand across two chains.

    Each product on either side is used at most once: the best-scoring pair wins,
    so one Mercadona row cannot claim every Dia variant of the same product.
    Community overrides are applied first and are never overruled.
    """
    forced, forbidden = overrides or ({}, set())
    by_sku_a = {str(r["sku"]): r for r in records_a}
    by_sku_b = {str(r["sku"]): r for r in records_b}

    equivalences: list[Equivalence] = []
    used_a: set[str] = set()
    used_b: set[str] = set()
    for (sku_a, sku_b), note in forced.items():
        record, other = by_sku_a.get(sku_a), by_sku_b.get(sku_b)
        if record is None or other is None:
            continue  # A product left the catalog; the override waits for its return.
        used_a.add(sku_a)
        used_b.add(sku_b)
        size = record_size(record)
        equivalences.append(
            Equivalence(
                brand=record.get("brand") or "",
                sku_a=sku_a,
                name_a=record["display_name"],
                sku_b=sku_b,
                name_b=other["display_name"],
                reference_format=record.get("reference_format") or "",
                size=size[0] if size else None,
                score=1.0,
                shared_terms=(note,) if note else (),
                method="community-override",
            )
        )

    prepared_b: dict[tuple[str, str], list[tuple[dict, frozenset, Any]]] = {}
    for record in records_b:
        brand, fmt = record.get("brand"), record.get("reference_format")
        if not brand or not fmt:
            continue
        key = (brand.strip().lower(), fmt)
        prepared_b.setdefault(key, []).append(
            (record, tokenize(record["display_name"], brand), record_size(record))
        )

    scored: list[tuple[float, dict, dict, frozenset, Any]] = []
    for record in records_a:
        brand, fmt = record.get("brand"), record.get("reference_format")
        if not brand or not fmt:
            continue
        tokens_a = tokenize(record["display_name"], brand)
        size_a = record_size(record)
        for other, tokens_b, size_b in prepared_b.get((brand.strip().lower(), fmt), []):
            if (str(record["sku"]), str(other["sku"])) in forbidden:
                continue
            if require_size and not sizes_agree(size_a, size_b):
                continue
            if numbers_conflict(record["display_name"], other["display_name"]):
                continue
            score = score_pair(tokens_a, tokens_b)
            if score >= min_score:
                scored.append((
                    score,
                    is_multipack(record) == is_multipack(other),
                    -size_gap(size_a, size_b),
                    record, other, tokens_a & tokens_b, size_a,
                ))

    # Best score first, and among equal scores the closest size. Without the
    # tie-break, two Mercadona rows sharing a name let an approximate size win:
    # a 4 L bottle pack claimed Dia's 12x330 ml cans (3.96 L, inside tolerance)
    # while the exact 3.96 L pack sat unmatched, inventing a 165% price gap.
    scored.sort(key=lambda row: (row[0], row[1], row[2]), reverse=True)
    for score, _, _, record, other, shared, size in scored:
        if str(record["sku"]) in used_a or str(other["sku"]) in used_b:
            continue
        used_a.add(str(record["sku"]))
        used_b.add(str(other["sku"]))
        equivalences.append(
            Equivalence(
                brand=record["brand"],
                sku_a=record["sku"],
                name_a=record["display_name"],
                sku_b=other["sku"],
                name_b=other["display_name"],
                reference_format=record["reference_format"],
                size=size[0] if size else None,
                score=score,
                shared_terms=tuple(sorted(shared)),
            )
        )
    return equivalences
