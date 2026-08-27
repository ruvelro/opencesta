from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

from opencesta.adapters import ADAPTERS
from opencesta.enrich import enrich_catalog
from opencesta.history import diff
from opencesta.ine import compare, fetch_series, span_series
from opencesta.judge import VerdictCache, judge_pairs, judging_is_available
from opencesta.match import (
    Equivalence,
    is_own_brand,
    load_for_matching,
    load_overrides,
    match_records,
    own_brand_candidates,
    record_size,
    resolve_conflicts,
    write_equivalences,
)
from opencesta.snapshot import snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opencesta")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="capture a chain/zone catalog to Parquet")
    snap.add_argument("--chain", default="mercadona", choices=sorted(ADAPTERS))
    snap.add_argument("--zone", required=True, help="warehouse/zone id, e.g. vlc1")
    snap.add_argument("--out", type=Path, default=Path("data"))

    enr = sub.add_parser("enrich", help="fill the SKU catalog (EAN/brand/origin) incrementally")
    enr.add_argument("--chain", default="mercadona", choices=sorted(ADAPTERS))
    enr.add_argument("--zone", required=True)
    enr.add_argument("--prices", type=Path, default=Path("data"),
                     help="directory holding the price snapshots")
    enr.add_argument("--catalog", type=Path, default=None,
                     help="catalog parquet (default: <prices>/catalog/<chain>.parquet)")
    enr.add_argument("--limit", type=int, default=None,
                     help="max SKUs to fetch this run (it resumes where it left off)")

    zone = sub.add_parser("zone", help="resolve a postal code to a zone id")
    zone.add_argument("postal_code")
    zone.add_argument("--chain", default="mercadona", choices=sorted(ADAPTERS))

    mat = sub.add_parser("match", help="pair national-brand products across two chains")
    mat.add_argument("--a", default="mercadona", choices=sorted(ADAPTERS))
    mat.add_argument("--zone-a", default="vlc1")
    mat.add_argument("--b", default="dia", choices=sorted(ADAPTERS))
    mat.add_argument("--zone-b", default="es-default")
    mat.add_argument("--prices", type=Path, default=Path("data"))
    mat.add_argument("--date", default=None, help="ISO date (default: newest common date)")
    # Kept distinct on purpose: overrides are hand-written and live in git, the
    # output is regenerated. Pointing both at one file would feed the computed
    # pairs back in as if a human had confirmed them.
    mat.add_argument("--overrides", type=Path, default=Path("overrides.jsonl"),
                     help="community corrections; they always win")
    mat.add_argument("--out", type=Path, default=None, help="write the pairs as JSONL")
    mat.add_argument("--min-score", type=float, default=0.5)
    mat.add_argument("--own-brands", action="store_true",
                     help="also pair own-brand products (no shared brand or EAN)")
    mat.add_argument("--judge", action="store_true",
                     help="send ambiguous own-brand pairs to Claude; verdicts are cached")
    mat.add_argument("--verdicts", type=Path, default=Path("verdicts.jsonl"),
                     help="cache of judgements, safe to commit")
    mat.add_argument("--accept-above", type=float, default=0.75,
                     help="own-brand score accepted without judging")
    mat.add_argument("--consider-above", type=float, default=0.4,
                     help="own-brand score below which pairs are dropped outright")

    inf = sub.add_parser("inflation", help="your measured basket change vs the INE food IPC")
    inf.add_argument("--chain", default="mercadona", choices=sorted(ADAPTERS))
    inf.add_argument("--zone", required=True)
    inf.add_argument("--prices", type=Path, default=Path("data"))
    inf.add_argument("--since", default=None)
    inf.add_argument("--until", default=None)

    dif = sub.add_parser("diff", help="compare two snapshots: price moves, new and delisted SKUs")
    dif.add_argument("--chain", default="mercadona", choices=sorted(ADAPTERS))
    dif.add_argument("--zone", required=True)
    dif.add_argument("--prices", type=Path, default=Path("data"))
    dif.add_argument("--since", default=None, help="ISO date (default: oldest snapshot)")
    dif.add_argument("--until", default=None, help="ISO date (default: newest snapshot)")
    dif.add_argument("--top", type=int, default=10, help="rows to show per direction")

    args = parser.parse_args(argv)

    if args.command == "snapshot":
        path = snapshot(args.chain, args.zone, args.out)
        print(path)
    elif args.command == "enrich":
        catalog = args.catalog or args.prices / "catalog" / f"{args.chain}.parquet"
        fetched, missing = enrich_catalog(args.chain, args.zone, args.prices, catalog, args.limit)
        print(f"fetched {fetched}, still missing {missing} ({catalog})")
    elif args.command == "zone":
        print(ADAPTERS[args.chain]().zone_for_postal_code(args.postal_code))
    elif args.command == "diff":
        _print_diff(diff(args.prices, args.chain, args.zone, args.since, args.until), args.top)
    elif args.command == "match":
        return _run_match(args)
    elif args.command == "inflation":
        return _print_inflation(
            diff(args.prices, args.chain, args.zone, args.since, args.until)
        )
    return 0


def _run_match(args) -> int:
    pairs, date = load_for_matching(
        args.prices, (args.a, args.zone_a), (args.b, args.zone_b), args.date
    )
    records_a, records_b = pairs
    equivalences = match_records(
        records_a,
        records_b,
        min_score=args.min_score,
        overrides=load_overrides(args.overrides),
    )
    by_sku_a = {str(r["sku"]): r for r in records_a}
    by_sku_b = {str(r["sku"]): r for r in records_b}

    print(f"{args.a}/{args.zone_a} vs {args.b}/{args.zone_b}  ({date})")
    print(f"{len(records_a)} x {len(records_b)} productos -> {len(equivalences)} equivalencias "
          f"en {len({e.brand for e in equivalences})} marcas")

    cheaper_a = cheaper_b = same = 0
    for equivalence in equivalences:
        price_a = by_sku_a[equivalence.sku_a].get("reference_price")
        price_b = by_sku_b[equivalence.sku_b].get("reference_price")
        if not price_a or not price_b:
            continue
        gap = (price_b - price_a) / price_a * 100
        if gap < -1:
            cheaper_b += 1
        elif gap > 1:
            cheaper_a += 1
        else:
            same += 1
    print(f"mismo precio: {same} | más barato en {args.a}: {cheaper_a} "
          f"| más barato en {args.b}: {cheaper_b}")

    if args.own_brands:
        equivalences += _match_own_brands(args, records_a, records_b)

    if args.out:
        print(f"\n{write_equivalences(equivalences, args.out)}")
    return 0


def _match_own_brands(args, records_a: list[dict], records_b: list[dict]) -> list:
    """Own brands: no EAN, no shared brand. High scores are taken, the middle is judged."""
    own_a = [r for r in records_a if is_own_brand(args.a, r)]
    own_b = [r for r in records_b if is_own_brand(args.b, r)]
    candidates = own_brand_candidates(own_a, own_b, min_score=args.consider_above)
    print(f"\nmarca propia: {len(own_a)} x {len(own_b)} productos, "
          f"{len(candidates)} pares candidatos")

    cache = VerdictCache(args.verdicts)
    judged: dict[tuple[str, str], object] = {}
    counts = {"auto": 0, "equivalent": 0, "substitute": 0, "different": 0}

    if args.judge:
        # Judge only pairs that survive conflict resolution, so we never pay to
        # judge a pair that a better-scoring one has already claimed.
        provisional = resolve_conflicts(candidates, lambda score, x, y: True)
        ambiguous = [
            (x["display_name"], y["display_name"])
            for score, x, y, _ in provisional
            if score < args.accept_above
        ]
        uncached = sum(1 for pair in set(ambiguous) if cache.get(*pair) is None)
        print(f"  {len(ambiguous)} en la banda ambigua ({uncached} sin veredicto en caché)")
        if uncached and not judging_is_available():
            print("  sin credenciales de Anthropic: se omite el juez y se descarta la banda")
        else:
            judged = judge_pairs(ambiguous, cache)

    def accept(score: float, record: dict, other: dict) -> bool:
        if score >= args.accept_above:
            counts["auto"] += 1
            return True
        verdict = judged.get((record["display_name"], other["display_name"]))
        if verdict is None:
            return False  # Unjudged ambiguity is dropped, never guessed.
        counts[verdict.verdict] += 1
        return verdict.verdict in ("equivalent", "substitute")

    kept = resolve_conflicts(candidates, accept)
    print(f"  aceptados: {counts['auto']} por score | {counts['equivalent']} equivalentes "
          f"y {counts['substitute']} sustitutos por el juez | "
          f"{counts['different']} descartados por el juez")

    equivalences = []
    for score, record, other, shared in kept:
        verdict = judged.get((record["display_name"], other["display_name"]))
        size = record_size(record)
        equivalences.append(
            Equivalence(
                brand=f"{record.get('brand') or '?'} / {other.get('brand') or '?'}",
                sku_a=str(record["sku"]),
                name_a=record["display_name"],
                sku_b=str(other["sku"]),
                name_b=other["display_name"],
                reference_format=record.get("reference_format") or "",
                size=size[0] if size else None,
                score=score,
                shared_terms=(verdict.reason,) if verdict else tuple(sorted(shared)),
                method=f"own-brand-{verdict.verdict}" if verdict else "own-brand-score",
            )
        )
    return equivalences


def _print_inflation(result: dict) -> int:
    since, until = result["since"], result["until"]
    days = (dt.date.fromisoformat(until) - dt.date.fromisoformat(since)).days
    basket = result["basket_pct"]
    print(f"{since} -> {until}  ({days} días, {result['tracked']} productos comparables)")
    print(f"tu cesta: {basket:+.2f}%")

    choice = span_series(days)
    if choice is None:
        print(
            f"\nSin comparación con el INE: {days} días no se corresponde con ninguna "
            "serie oficial.\nHacen falta ~30 días de histórico para la variación "
            "mensual, o ~365 para la anual."
        )
        return 0

    series, _ = choice
    rows = fetch_series(series, last=1)
    if not rows:
        print("\nEl INE no devolvió datos para esa serie.")
        return 1
    official = rows[-1]
    verdict = compare(basket, official["value"])
    label = "mensual" if series == "monthly" else "anual"
    print(f"IPC de alimentación ({label}, {official['period']}): {official['value']:+.2f}%")
    print(f"\ntu cesta va {verdict['gap_pct']:+.2f} puntos {verdict['verdict']}")
    return 0


def _print_diff(result: dict, top: int) -> None:
    since, until = result["since"], result["until"]
    changed = result["changed"]
    print(f"{since} -> {until}  ({result['tracked']} productos en ambas fechas)")
    print(f"cambios de precio: {changed.height} | nuevos: {result['added'].height} "
          f"| descatalogados: {result['removed'].height}")
    if result["basket_pct"] is not None:
        print(f"variación del catálogo comparable: {result['basket_pct']:+.2f}%")
    for label, rows in (("bajadas", changed.head(top)), ("subidas", changed.tail(top).reverse())):
        if not rows.height:
            continue
        print(f"\nmayores {label}:")
        for row in rows.iter_rows(named=True):
            print(f"  {row['pct']:+6.1f}%  {row[since]:>7.2f} -> {row[until]:>7.2f}  "
                  f"{row['display_name'][:52]}")


if __name__ == "__main__":
    sys.exit(main())
