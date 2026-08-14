"""
drawing_pipeline_flag.py — Surface parts that still need the drawing pipeline.

Why this exists
---------------
Running the drawing pipeline is a manual step that is easy to forget. A part in
`parts_control` with no assembly-pack association is the signal that it has not
been done. This module raises that as a visible priority in the purchasing email
pipeline. It FLAGS ONLY — it never runs the drawing pipeline and never edits
Asana.

Why this is a standing list, not a per-email flag
-------------------------------------------------
The intended design was to flag the specific parts attached to each inbound
email. That is not currently possible: `parts_control.procurement_task_gid` —
the only column joining a part to a procurement task (and therefore to a matched
email) — is empty in all 2,104 rows. Until something populates it, there is no
data path from an email to its parts.

So instead this writes a standing, refreshed-every-run list to its own sheet tab.
It surfaces the same information and cannot be forgotten, but it is scoped to
"these parts need the drawing pipeline", not "this email needs it". If
procurement_task_gid is ever populated, add_per_email_flag() below becomes
implementable and this can be narrowed.

Usage:
    python drawing_pipeline_flag.py --dry-run      # print, write nothing
    python drawing_pipeline_flag.py --json out.json
    python drawing_pipeline_flag.py                # write to Google Sheet tab
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, r"C:\Users\GilesHugo\SES_Workspace\lib")

import pack_association

try:
    from path_config import p as _p
    DEFAULT_DB_PATH = _p("db.ops")
except Exception:  # pragma: no cover
    DEFAULT_DB_PATH = None

# Same sheet as the approval dashboard, separate tab so the Pending tab's
# column layout (read by 05_sheet_approver) is untouched.
SHEET_ID = "1fvoSKnvH0vdM_aZbo2_seY6AU3SZaXABaxRUKSxWVlo"
FLAG_TAB = "Drawing Pipeline Required"
SERVICE_ACCOUNT_PATH = r"C:\Users\GilesHugo\.hermes\Google_Service_Account.json"

HEADERS = ["Part Number", "Part Name", "Vendor", "Qty",
           "Stock Action", "Project", "Asana Task GID", "Last Synced"]


def _clean(value) -> str:
    """Part names carry embedded tabs/newlines that corrupt sheet rows."""
    if value is None:
        return ""
    return " ".join(str(value).replace("\t", " ").split())


def collect(db_path: str) -> list[dict]:
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        pack_association.classify(conn)
        return pack_association.get_flagged_parts(conn, open_only=True)
    finally:
        conn.close()


def to_rows(parts: list[dict]) -> list[list[str]]:
    return [[
        _clean(p.get("sesame_pn")),
        _clean(p.get("part_name")),
        _clean(p.get("vendor_name")),
        _clean(p.get("order_qty")),
        _clean(p.get("stock_action")),
        _clean(p.get("project_name")),
        _clean(p.get("asana_task_gid")),
        _clean(p.get("last_synced_at")),
    ] for p in parts]


def write_to_sheet(rows: list[list[str]]) -> None:
    try:
        import gspread
    except ImportError:
        print("ERROR: gspread not installed.")
        print(r"Fix: C:\Users\GilesHugo\SES_Workspace\.venv\Scripts\pip.exe install gspread")
        sys.exit(1)

    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        print(f"ERROR: Google credentials not found at {SERVICE_ACCOUNT_PATH}")
        sys.exit(1)

    gc = gspread.service_account(filename=SERVICE_ACCOUNT_PATH)
    sh = gc.open_by_key(SHEET_ID)
    try:
        ws = sh.worksheet(FLAG_TAB)
        ws.clear()  # standing list — always reflects current state
    except Exception:
        ws = sh.add_worksheet(title=FLAG_TAB, rows=max(len(rows) + 10, 100),
                              cols=len(HEADERS))

    ws.update([HEADERS] + rows, value_input_option="RAW")
    print(f"Sheet tab '{FLAG_TAB}' refreshed: {len(rows)} part(s) need the drawing pipeline.")


def add_per_email_flag(*_args, **_kwargs):
    """Placeholder — see module docstring.

    Blocked on parts_control.procurement_task_gid being populated. Deliberately
    raises rather than silently returning "no parts flagged", which would look
    like a clean result while actually meaning "no data".
    """
    raise NotImplementedError(
        "Per-email flagging requires parts_control.procurement_task_gid, "
        "which is currently empty for all rows."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=DEFAULT_DB_PATH)
    ap.add_argument("--dry-run", action="store_true", help="Print only, write nothing")
    ap.add_argument("--json", metavar="PATH", help="Write the flagged list to JSON")
    args = ap.parse_args()

    if not args.db_path or not Path(args.db_path).exists():
        print(f"ERROR: database not found at {args.db_path}")
        sys.exit(1)

    parts = collect(args.db_path)
    rows = to_rows(parts)
    print(f"PRIORITY: {len(rows)} open part(s) with no assembly pack "
          f"— drawing pipeline likely required.")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(parts, f, indent=2)
        print(f"Written to {args.json}")

    if args.dry_run:
        for r in rows[:20]:
            print(f"  {r[0][:18]:18} | {r[1][:40]:40} | {r[5][:20]}")
        if len(rows) > 20:
            print(f"  … and {len(rows) - 20} more")
        return

    write_to_sheet(rows)


if __name__ == "__main__":
    main()
