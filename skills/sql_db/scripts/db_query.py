"""
db_query.py — Read-only query interface for the OpenClaw SQLite databases.

Accepts a SQL SELECT statement and returns results as JSON to stdout.
Only SELECT statements are permitted — any other statement type will be rejected.

Usage:
    python db_query.py "SELECT * FROM leads LIMIT 10" --db crm
    python db_query.py "SELECT * FROM scraper_runs" --db ops
    python db_query.py --db-path "C:/custom/crm.db" "SELECT campaign_name, AVG(metric_value) FROM campaign_results GROUP BY campaign_name"

--db flag accepts: ops, crm (default: ops)
  ops  → "C:/Users/GilesHugo/SES_Workspace/db/ops.db"  (env: OPENCLAW_OPS_DB_PATH)
  crm  → "C:/Users/GilesHugo/SES_Workspace/db/crm.db"  (env: OPENCLAW_CRM_DB_PATH)

Output (stdout, JSON):
    {"rows": [...], "count": 42, "query": "SELECT ..."}

Errors go to stderr. Exit code 0 on success, 1 on error.
"""

import argparse
import json
import logging
import os
import sqlite3
import sys

from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p

DB_DEFAULTS = {
    "ops": os.environ.get("OPENCLAW_OPS_DB_PATH") or _p("db.ops"),
    "crm": os.environ.get("OPENCLAW_CRM_DB_PATH") or _p("db.crm"),
}

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def is_select(sql: str) -> bool:
    return sql.strip().upper().startswith("SELECT")


def run_query(db_path: str, sql: str) -> dict:
    if not is_select(sql):
        raise ValueError("Only SELECT statements are permitted by db_query.py. Use db_write.py for writes.")

    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Database not found at {db_path}. Run db_init.py first."
        )

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(sql)
        rows = [dict(row) for row in cursor.fetchall()]
        return {"rows": rows, "count": len(rows), "query": sql}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a read-only SQL query against an OpenClaw database.")
    parser.add_argument("sql", help="SQL SELECT statement to execute.")
    parser.add_argument("--db", choices=["ops", "crm"], default="ops", help="Which database to query: ops or crm. Default: ops.")
    parser.add_argument("--db-path", default=None, help="Explicit path to the SQLite database file. Overrides --db.")
    args = parser.parse_args()

    db_path = args.db_path if args.db_path else DB_DEFAULTS[args.db]

    try:
        result = run_query(db_path, args.sql)
        print(json.dumps(result, indent=2, default=str))
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as exc:
        print(f"SQL ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"UNEXPECTED ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
