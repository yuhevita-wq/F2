#!/usr/bin/env python3
"""Query annual F2 SQLite data; gunzip on demand. Standard library only."""
from __future__ import annotations

import argparse
import csv
import gzip
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / "data" / "local"
READY = ROOT / "data" / "ready"


def database(year: int) -> Path:
    local = LOCAL / f"f2_{year}.sqlite"
    packed = READY / f"f2_{year}.sqlite.gz"
    if packed.exists() and (not local.exists() or packed.stat().st_mtime > local.stat().st_mtime):
        LOCAL.mkdir(parents=True, exist_ok=True)
        partial = local.with_name(local.name + ".partial")
        with gzip.open(packed, "rb") as src, partial.open("wb") as dest:
            shutil.copyfileobj(src, dest)
        partial.replace(local)
    if not local.exists():
        raise FileNotFoundError(f"{local} not found. Run python scripts/build_f2.py --year {year}")
    return local


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", choices=("2025", "2026", "all"), default="2025")
    parser.add_argument("--sql", required=True, help="Read-only SQL query")
    parser.add_argument("--output", help="Optional CSV export path")
    parser.add_argument("--limit", type=int, default=40, help="Printed preview row limit")
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be at least 1")
    years = (2025, 2026) if args.year == "all" else (int(args.year),)
    conn = sqlite3.connect(":memory:")
    try:
        for year in years:
            conn.execute(f"ATTACH DATABASE ? AS y{year}", (f"file:{database(year)}?mode=ro",))
        if len(years) == 1:
            # Keep the examples identical to a normal unqualified SQLite database.
            conn.close()
            conn = sqlite3.connect(f"file:{database(years[0])}?mode=ro", uri=True)
        conn.execute("PRAGMA query_only=ON")
        cursor = conn.execute(args.sql)
        columns = [col[0] for col in cursor.description or []]
        if not columns:
            raise ValueError("The query did not return any columns")
        if args.output:
            path = Path(args.output)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(columns)
                for row in cursor:
                    writer.writerow(row)
            print(f"Exported CSV: {path}")
        else:
            print("\t".join(columns))
            rows = cursor.fetchmany(args.limit + 1)
            for row in rows[:args.limit]:
                print("\t".join("" if x is None else str(x) for x in row))
            if len(rows) > args.limit:
                print(f"... more rows omitted; use --output to export all")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
