"""Loader CLI. One command per dataset, safe to re-run.

    python -m app.datasets.cli status
    python -m app.datasets.cli nhamcs  data/raw/nhamcs/*.csv
    python -m app.datasets.cli neiss   data/raw/neiss/*.csv
    python -m app.datasets.cli faers   data/raw/faers/2024q1 data/raw/faers/2024q2

Each loader rebuilds its own tables and leaves the others alone, so the data
track can land NHAMCS while FAERS is still downloading.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import sys

from . import faers, neiss, nhamcs
from .warehouse import db_path, status


def _expand(patterns: list[str]) -> list[str]:
    paths: list[str] = []
    for pattern in patterns:
        matches = glob.glob(pattern)
        if not matches:
            print(f"no files matched {pattern!r}", file=sys.stderr)
        paths.extend(sorted(matches))
    if not paths:
        raise SystemExit("nothing to load")
    return paths


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(prog="app.datasets.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="what is loaded")

    for name, help_text in (
        ("nhamcs", "CDC NHAMCS ED public-use CSVs"),
        ("neiss", "CPSC NEISS injury CSV/XLSX files"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("paths", nargs="+")
        p.add_argument("--min-n", type=int, default=30,
                       help="drop cells thinner than this (default 30)")

    p = sub.add_parser("faers", help="FDA FAERS quarter folders (unzipped ASCII)")
    p.add_argument("paths", nargs="+")
    p.add_argument("--min-reports", type=int, default=20)

    args = parser.parse_args(argv)

    if args.command == "status":
        print(json.dumps(status(), indent=2))
        return 0

    if args.command == "nhamcs":
        rows = nhamcs.load(_expand(args.paths), min_n=args.min_n)
        print(f"nhamcs_senior_rates: {rows} cells -> {db_path()}")
    elif args.command == "neiss":
        rows = neiss.load(_expand(args.paths), min_n=args.min_n)
        print(f"neiss_senior_rates: {rows} cells -> {db_path()}")
    elif args.command == "faers":
        counts = faers.load(args.paths, min_reports=args.min_reports)
        print(json.dumps(counts, indent=2))

    from .lookup import clear_cache

    clear_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
