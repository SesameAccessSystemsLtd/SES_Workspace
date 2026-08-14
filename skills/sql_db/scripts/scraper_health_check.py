"""
scraper_health_check.py — Surface silent scraper failures.

Checks scraper_runs in openclaw.db for recent failures and scans DATAMAP.md
for ERROR/STALE entries. Sends a Telegram alert if anything needs attention.
Writes to SHARED_CONTEXT.md Cross-Agent Blockers section if blockers are found.

Usage:
    python scraper_health_check.py                  # last 7 days
    python scraper_health_check.py --days 1         # last 24 hours only
    python scraper_health_check.py --no-telegram    # dry run, print only
    python scraper_health_check.py --clear-blockers # remove resolved blockers

Exit code 0 = all clear, 1 = failures found (caller can act on this).
"""

import argparse
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p

OPENCLAW_ROOT = Path(r"C:\Users\GilesHugo\SES_Workspace")
DB_PATH       = Path(_p("db.ops"))
DATAMAP_PATH  = OPENCLAW_ROOT / "workspace" / "DATAMAP.md"
CONTEXT_PATH  = OPENCLAW_ROOT / "workspace" / "SHARED_CONTEXT.md"
NOTIFY_SCRIPT = OPENCLAW_ROOT / "workspace" / "skills" / "purchasing" / "notify_telegram.py"

# Imported lazily so the script works even if requests isn't installed
_send_telegram = None


def _load_telegram():
    global _send_telegram
    if _send_telegram is not None:
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("notify_telegram", NOTIFY_SCRIPT)
    mod  = importlib.util.load_from_spec(spec)
    spec.loader.exec_module(mod)
    _send_telegram = mod.send_telegram


def telegram(text: str, use_telegram: bool) -> None:
    if not use_telegram:
        print(f"[DRY RUN] Telegram:\n{text}\n")
        return
    try:
        _load_telegram()
        ok = _send_telegram(text)
        if not ok:
            print("[scraper_health] Telegram send failed (non-fatal)", file=sys.stderr)
    except Exception as e:
        print(f"[scraper_health] Telegram unavailable: {e}", file=sys.stderr)


def check_db(days: int) -> list[dict]:
    """Return failed scraper_runs from the last N days."""
    if not DB_PATH.exists():
        return [{"dataset_name": "N/A", "pulled_at": "unknown",
                 "status": "ERROR", "notes": f"Database not found: {DB_PATH}"}]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "SELECT dataset_name, pulled_at, status, notes FROM scraper_runs "
        "WHERE status != 'success' AND pulled_at >= ? ORDER BY pulled_at DESC",
        (cutoff,)
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def check_datamap() -> list[str]:
    """Return dataset names marked ERROR in DATAMAP.md."""
    if not DATAMAP_PATH.exists():
        return ["DATAMAP.md missing"]
    text = DATAMAP_PATH.read_text(encoding="utf-8")
    # Match table rows containing ERROR status
    return re.findall(r"\|\s*([\w_]+)\s*\|[^|]*\|\s*ERROR", text)


def write_shared_context_blocker(blockers: list[str], clear: bool) -> None:
    if not CONTEXT_PATH.exists():
        return
    text = CONTEXT_PATH.read_text(encoding="utf-8")
    section_marker = "## Cross-Agent Blockers"
    if section_marker not in text:
        return

    # Remove old scraper_health lines
    lines = text.splitlines()
    cleaned = [l for l in lines if "[scraper_health]" not in l]

    if not clear and blockers:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        insert_after = next(
            (i for i, l in enumerate(cleaned) if section_marker in l), None
        )
        if insert_after is not None:
            for b in reversed(blockers):
                cleaned.insert(insert_after + 1,
                    f"- [{ts}] [scraper_health] {b}")

    CONTEXT_PATH.write_text("\n".join(cleaned), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=7,
                        help="How many days back to check (default: 7)")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Print only, do not send Telegram message")
    parser.add_argument("--clear-blockers", action="store_true",
                        help="Remove resolved scraper_health blockers from SHARED_CONTEXT.md")
    args = parser.parse_args()

    use_telegram = not args.no_telegram
    failed_runs  = check_db(args.days)
    error_maps   = check_datamap()

    if args.clear_blockers:
        write_shared_context_blocker([], clear=True)
        print("Cleared scraper_health blockers from SHARED_CONTEXT.md")
        return

    if not failed_runs and not error_maps:
        print("✅ Scraper health OK — no failures found.")
        return 0

    # Build alert text
    lines = ["[SCRAPER HEALTH] Failures detected\n"]

    if failed_runs:
        lines.append("<b>Failed runs (last {} days):</b>".format(args.days))
        for r in failed_runs:
            lines.append(
                f"  • <b>{r['dataset_name']}</b> [{r['pulled_at'][:16]}] "
                f"— {r['status'].upper()}: {(r['notes'] or 'no detail')[:120]}"
            )
        lines.append("")

    if error_maps:
        lines.append("<b>DATAMAP.md ERROR entries:</b>")
        for d in error_maps:
            lines.append(f"  • {d}")
        lines.append("")

    lines.append("\nAction: scraper_agent should re-run failed datasets and update DATAMAP.md.")
    alert_text = "\n".join(lines)

    print(alert_text.replace("<b>", "").replace("</b>", ""))
    telegram(alert_text, use_telegram)

    # Surface in SHARED_CONTEXT.md
    blocker_entries = []
    for r in failed_runs:
        blocker_entries.append(
            f"scraper_agent pull FAILED for '{r['dataset_name']}' "
            f"({r['pulled_at'][:16]}) — {(r['notes'] or 'no detail')[:100]}"
        )
    for d in error_maps:
        blocker_entries.append(f"DATAMAP.md ERROR: {d}")
    write_shared_context_blocker(blocker_entries, clear=False)

    return 1


if __name__ == "__main__":
    sys.exit(main())
