from __future__ import annotations

import argparse
import sys
from pathlib import Path

from opencesta.adapters import ADAPTERS
from opencesta.snapshot import snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="opencesta")
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="capture a chain/zone catalog to Parquet")
    snap.add_argument("--chain", default="mercadona", choices=sorted(ADAPTERS))
    snap.add_argument("--zone", required=True, help="warehouse/zone id, e.g. vlc1")
    snap.add_argument("--out", type=Path, default=Path("data"))

    zone = sub.add_parser("zone", help="resolve a postal code to a zone id")
    zone.add_argument("postal_code")
    zone.add_argument("--chain", default="mercadona", choices=sorted(ADAPTERS))

    args = parser.parse_args(argv)

    if args.command == "snapshot":
        path = snapshot(args.chain, args.zone, args.out)
        print(path)
    elif args.command == "zone":
        print(ADAPTERS[args.chain]().zone_for_postal_code(args.postal_code))
    return 0


if __name__ == "__main__":
    sys.exit(main())
