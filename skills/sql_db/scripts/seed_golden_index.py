"""
seed_golden_index.py — Seed ops.db golden_index table from Golden Index CSV (Rev4+).

Reads the Golden Index CSV and upserts each row into golden_index.
Safe to re-run — upserts on sesame_part_no (UNIQUE).

Rev4 CSV columns used:
    Stock_Number        → sesame_part_no  (already prefix-stripped, e.g. 1003.B.087)
    Mapped_KAN          → kan_number
    Asana_Name          → description
    Asana_URL           → asana_task_url  (GID also extracted)
    Confidence          → match_confidence
    Asana_TYPE_OF_STOCK → stock_project
    Match_Type          → stored in notes

Usage:
    python seed_golden_index.py
    python seed_golden_index.py --csv "C:\\path\\to\\Merged_Golden_Index_Rev4.csv"
    python seed_golden_index.py --db-path "C:\\custom\\path\\ops.db"
    python seed_golden_index.py --clear          # truncate table before seeding
"""

import argparse
import csv
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone

import sys as _sys
from pathlib import Path as _Path
_sys.path.insert(0, str(_Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p

DEFAULT_DB_PATH = os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops")
DEFAULT_CSV_PATH = (
    r"C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline"
    r"\2.Stock Controls\Merged_Golden_Index_Rev4.csv"
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def extract_gid(url: str) -> str:
    """Extract Asana task GID from a URL. Returns empty string if not found."""
    if not url:
        return ""
    url = str(url).strip()
    url = re.sub(r"\.0$", "", url)
    parts = url.rstrip("/f").rstrip("/").split("/")
    for part in reversed(parts):
        clean = re.sub(r"\.0$", "", part)
        if re.match(r"^\d{10,}$", clean):
            return clean
    return ""


def seed(csv_path: str, db_path: str, clear: bool = False) -> None:
    if not os.path.exists(csv_path):
        log.error("CSV not found: %s", csv_path)
        raise FileNotFoundError(csv_path)

    conn = sqlite3.connect(db_path)
    now = datetime.now(timezone.utc).isoformat()

    try:
        if clear:
            conn.execute("DELETE FROM golden_index")
            conn.commit()
            log.info("golden_index table cleared.")

        skipped = 0
        row_count = 0

        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                part_no = (row.get("Stock_Number") or "").strip()
                if not part_no:
                    skipped += 1
                    continue

                asana_url = (row.get("Asana_URL") or "").strip()
                gid = extract_gid(asana_url)

                try:
                    confidence = int(float(row.get("Confidence") or 0))
                except (ValueError, TypeError):
                    confidence = None

                stock_project = (row.get("Asana_TYPE_OF_STOCK") or "").strip() or None
                match_type = (row.get("Match_Type") or "").strip() or None

                conn.execute(
                    """
                    INSERT INTO golden_index
                        (sesame_part_no, kan_number, asana_task_gid, asana_task_url,
                         stock_project, description, match_confidence, last_synced, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sesame_part_no) DO UPDATE SET
                        kan_number        = excluded.kan_number,
                        asana_task_gid    = excluded.asana_task_gid,
                        asana_task_url    = excluded.asana_task_url,
                        stock_project     = excluded.stock_project,
                        description       = excluded.description,
                        match_confidence  = excluded.match_confidence,
                        last_synced       = excluded.last_synced,
                        updated_at        = excluded.updated_at
                    """,
                    (
                        part_no,
                        (row.get("Mapped_KAN") or "").strip() or None,
                        gid or None,
                        asana_url or None,
                        stock_project,
                        (row.get("Asana_Name") or "").strip() or None,
                        confidence,
                        now,
                        now,
                    ),
                )
                row_count += 1

        conn.commit()
        cursor = conn.execute("SELECT COUNT(*) FROM golden_index")
        total = cursor.fetchone()[0]
        log.info(
            "Seed complete. Processed: %d | Skipped (no part no): %d | Total rows in table: %d",
            row_count, skipped, total,
        )
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed ops.db golden_index from Golden Index CSV (Rev4+)."
    )
    parser.add_argument("--csv", default=DEFAULT_CSV_PATH, help="Path to Golden Index CSV.")
    parser.add_argument("--db-path", default=DEFAULT_DB_PATH, help="Path to ops.db.")
    parser.add_argument(
        "--clear", action="store_true",
        help="Truncate the golden_index table before seeding. Use this to replace stale data."
    )
    args = parser.parse_args()
    seed(args.csv, args.db_path, clear=args.clear)


if __name__ == "__main__":
    main()
