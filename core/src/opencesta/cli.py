from __future__ import annotations

import argparse
import sys
from pathlib import Path

from opencesta.adapters import ADAPTERS
from opencesta.enrich import enrich_catalog
from opencesta.history import diff
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
