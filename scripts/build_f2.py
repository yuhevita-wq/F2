#!/usr/bin/env python3
"""Build annual F2-only SQLite databases from pinned public source CSVs.

No scraping, guessing or modifications to the upstream repository.
Only A1/A2 and A3 challenge races at explicitly F2-graded meetings are imported.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "source_manifest.json"
LOCAL = ROOT / "data" / "local"
READY = ROOT / "data" / "ready"
REPORTS = ROOT / "reports" / "ingestion"
TABLES = ("races", "entries", "results", "payouts")
META = ("f2_class", "source_period", "source_file")
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def quote(column: str) -> str:
    if not IDENTIFIER.fullmatch(column):
        raise ValueError(f"Unexpected CSV column: {column!r}")
    return '"' + column + '"'


def norm(value: str | None) -> str:
    return unicodedata.normalize("NFKC", value or "").strip()


def eligible(race: dict[str, str], source: dict) -> bool:
    """Keep F2 only, preserving distinct A1/A2 versus A3 challenge labels."""
    if not (source["start_date"] <= race["race_date"] <= source["end_date"]):
        raise ValueError(f"Race outside source period: {source['id']} {race['race_id']}")
    if norm(race.get("meeting_grade")).upper() != "F2":
        return False
    cl = source["class"]
    if cl == "A12":
        if not norm(race.get("race_type")).startswith("A級"):
            raise ValueError(f"Not an A-class race: {race['race_id']}")
        if norm(race.get("class_scope")) != "A1/A2":
            raise ValueError(f"Not A1/A2: {race['race_id']}")
    elif cl == "A3":
        if "チャレンジ" not in norm(race.get("race_type")):
            raise ValueError(f"Not a challenge race: {race['race_id']}")
        if norm(race.get("class_scope")) != "A3 challenge":
            raise ValueError(f"Not A3 challenge: {race['race_id']}")
    else:
        raise ValueError(f"Unknown source class: {cl}")
    return True


def load_manifest() -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    seen_ids, seen_paths = set(), set()
    periods: dict[tuple[int, str], list[tuple[str, str]]] = {}
    for source in manifest["sources"]:
        if source["id"] in seen_ids or source["path"] in seen_paths:
            raise ValueError("Repeated source id/path in manifest")
        seen_ids.add(source["id"])
        seen_paths.add(source["path"])
        if source["class"] not in ("A12", "A3") or int(source["start_date"][:4]) != source["year"]:
            raise ValueError(f"Invalid class/year: {source}")
        if not source["start_date"] <= source["end_date"]:
            raise ValueError(f"Invalid period: {source}")
        periods.setdefault((source["year"], source["class"]), []).append(
            (source["start_date"], source["end_date"]))
    for key, intervals in periods.items():
        intervals.sort()
        for a, b in zip(intervals, intervals[1:]):
            if a[1] >= b[0]:
                raise ValueError(f"Overlapping source intervals: {key}: {a}, {b}")
    if not re.fullmatch(r"[0-9a-f]{40}", manifest["source_ref"]):
        raise ValueError("Source ref must be immutable 40-digit commit SHA")
    return manifest


def fetch_csv(manifest: dict, source: dict, filename: str, dst: Path) -> dict:
    repo = manifest["source_repository"]
    ref = manifest["source_ref"]
    path = source["path"] + "/" + filename
    url = f"https://raw.githubusercontent.com/{repo}/{ref}/{path}"
    tmp = dst.with_suffix(".partial")
    for retry in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "F2-dataset-build/1.0"})
            digest, size = hashlib.sha256(), 0
            with urllib.request.urlopen(req, timeout=180) as response, tmp.open("wb") as out:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    out.write(chunk)
                    size += len(chunk)
            if size == 0:
                raise ValueError(f"Empty CSV: {path}")
            tmp.replace(dst)
            return {"path": path, "bytes": size, "sha256": digest.hexdigest()}
        except Exception:
            tmp.unlink(missing_ok=True)
            if retry == 3:
                raise
            time.sleep(2 * (retry + 1))
    raise AssertionError("unreachable")


def reader_for(path: Path):
    """Reader lifetime is controlled by caller with the returned file handle."""
    handle = path.open(encoding="utf-8-sig", newline="")
    reader = csv.DictReader(handle)
    columns = reader.fieldnames
    if not columns or len(columns) != len(set(columns)) or "race_id" not in columns:
        handle.close()
        raise ValueError(f"Invalid CSV header: {path}")
    if any(name in META for name in columns):
        handle.close()
        raise ValueError(f"CSV metadata-column collision: {path}")
    for name in columns:
        quote(name)
    return handle, reader, columns


def ensure_table(db: sqlite3.Connection, table: str, columns: list[str]) -> None:
    known = {r[1] for r in db.execute(f"PRAGMA table_info({quote(table)})")}
    if not known:
        defs = ", ".join(f"{quote(c)} TEXT" for c in [*columns, *META])
        db.execute(f"CREATE TABLE {quote(table)} ({defs})")
    else:
        for column in columns:
            if column not in known:
                db.execute(f"ALTER TABLE {quote(table)} ADD COLUMN {quote(column)} TEXT")


def import_csv(db: sqlite3.Connection, path: Path, table: str, source: dict,
               selected: set[str] | None) -> tuple[int, set[str], int]:
    handle, reader, columns = reader_for(path)
    imported, excluded = 0, 0
    picked = set()
    try:
        ensure_table(db, table, columns)
        all_cols = [*columns, *META]
        stmt = (f"INSERT INTO {quote(table)} (" +
                ",".join(quote(c) for c in all_cols) + ") VALUES (" +
                ",".join("?" for _ in all_cols) + ")")
        batch = []
        for row in reader:
            if None in row or not row.get("race_id"):
                raise ValueError(f"Malformed CSV row in {path}")
            rid = row["race_id"]
            if table == "races":
                use = eligible(row, source)
            else:
                use = rid in selected  # selected is fixed from the matching races.csv
            if not use:
                excluded += 1
                continue
            if table == "races":
                if rid in picked:
                    raise ValueError(f"Duplicate race_id in source: {rid}")
                picked.add(rid)
            values = [row[c] for c in columns] + [
                source["class"], source["id"], source["path"] + "/" + table + ".csv"]
            batch.append(values)
            if len(batch) >= 5000:
                db.executemany(stmt, batch)
                imported += len(batch)
                batch.clear()
        if batch:
            db.executemany(stmt, batch)
            imported += len(batch)
        return imported, picked, excluded
    finally:
        handle.close()


def integrity(db: sqlite3.Connection) -> dict:
    db.execute("CREATE UNIQUE INDEX uq_races_id ON races(race_id)")
    db.execute("CREATE UNIQUE INDEX uq_entries_rider ON entries(race_id,car_no)")
    db.execute("CREATE UNIQUE INDEX uq_results_rider ON results(race_id,car_no)")
    for table in TABLES:
        db.execute(f"CREATE INDEX ix_{table}_id ON {table}(race_id)")
    db.execute("CREATE INDEX ix_races_date ON races(race_date)")
    db.execute("CREATE INDEX ix_races_grade_class ON races(meeting_grade,f2_class)")
    for table in ("entries", "results", "payouts"):
        orphans = db.execute(f"""
          SELECT COUNT(*) FROM {table} AS t
          LEFT JOIN races r ON t.race_id = r.race_id WHERE r.race_id IS NULL
        """).fetchone()[0]
        if orphans:
            raise ValueError(f"Orphan {table} rows: {orphans}")
    mismatched = db.execute("""
      SELECT COUNT(*) FROM races WHERE UPPER(meeting_grade) != 'F2'
    """).fetchone()[0]
    if mismatched:
        raise ValueError(f"Non-F2 races in database: {mismatched}")
    db.executescript("""
      CREATE VIEW v_pre_race_races AS SELECT * FROM races;
      CREATE VIEW v_pre_race_entries AS SELECT * FROM entries;
      CREATE VIEW v_results AS SELECT * FROM results;
      CREATE VIEW v_paid_trifecta AS
        SELECT race_id,race_date,combination,
               CAST(payout_yen AS INTEGER) AS payout_yen,
               popularity,f2_class,source_period
          FROM payouts WHERE ticket_type='3連単' AND status='paid';
    """)
    result = {"row_counts": {}, "by_class": {}, "races_missing": {}}
    for table in TABLES:
        result["row_counts"][table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    for cl, count in db.execute("SELECT f2_class,COUNT(*) FROM races GROUP BY f2_class"):
        result["by_class"][cl] = count
    for table in ("entries", "results", "payouts"):
        result["races_missing"][table] = db.execute(f"""
          SELECT COUNT(*) FROM races r WHERE NOT EXISTS(
            SELECT 1 FROM {table} x WHERE x.race_id=r.race_id)
        """).fetchone()[0]
    return result


def build_year(manifest: dict, year: int) -> dict:
    sources = [s for s in manifest["sources"] if s["year"] == year]
    if not sources:
        raise ValueError(f"No sources for {year}")
    for p in (LOCAL, READY, REPORTS):
        p.mkdir(parents=True, exist_ok=True)
    temp_db = LOCAL / f"f2_{year}.building.sqlite"
    temp_db.unlink(missing_ok=True)
    report = {
        "year": year, "source_repository": manifest["source_repository"],
        "source_ref": manifest["source_ref"], "source_files": [], "periods": {},
        "filter": "meeting_grade == F2; A12=A級+class_scope A1/A2; A3=チャレンジ+class_scope A3 challenge",
        "coverage_warning": "Source periods are not proof of nationwide completeness.",
        "post_race_warning": "Results/payouts are known only after the race.",
    }
    db = sqlite3.connect(temp_db)
    try:
        db.execute("PRAGMA journal_mode=DELETE")
        db.execute("PRAGMA synchronous=NORMAL")
        with tempfile.TemporaryDirectory(prefix="f2_sources_") as td:
            scratch = Path(td)
            for source in sources:
                counts, selected = {}, None
                for table in TABLES:
                    filename = table + ".csv"
                    local = scratch / filename
                    meta = fetch_csv(manifest, source, filename, local)
                    report["source_files"].append(meta)
                    count, ids, excluded = import_csv(
                        db, local, table, source, selected)
                    counts[table] = {"selected_rows": count, "excluded_rows": excluded}
                    if table == "races":
                        selected = ids
                        if not selected:
                            raise ValueError(f"Source yielded zero F2 races: {source['id']}")
                    local.unlink()
                db.commit()
                report["periods"][source["id"]] = counts
                print(f"Imported {source['id']}: {counts}", flush=True)
        report["integrity"] = integrity(db)
        check = db.execute("PRAGMA integrity_check").fetchone()[0]
        if check != "ok":
            raise RuntimeError(f"SQLite integrity_check: {check}")
        db.commit()
        db.execute("VACUUM")
    except Exception:
        db.close()
        temp_db.unlink(missing_ok=True)
        raise
    db.close()
    uncompressed_bytes = temp_db.stat().st_size
    output = READY / f"f2_{year}.sqlite.gz"
    temp_gz = output.with_name(output.name + ".partial")
    with temp_db.open("rb") as src, gzip.open(temp_gz, "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    temp_gz.replace(output)
    temp_db.replace(LOCAL / f"f2_{year}.sqlite")
    report["database"] = {"path": str(output.relative_to(ROOT)),
                           "gz_bytes": output.stat().st_size,
                           "sqlite_bytes": uncompressed_bytes}
    report_path = REPORTS / f"{year}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")
    print(f"BUILT {output}, races={report['integrity']['row_counts']['races']}", flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", choices=("2025", "2026", "all"), default="all")
    args = parser.parse_args()
    manifest = load_manifest()
    years = sorted({s["year"] for s in manifest["sources"]})
    if args.year != "all":
        years = [int(args.year)]
    for year in years:
        build_year(manifest, year)


if __name__ == "__main__":
    main()
