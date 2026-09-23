"""Backfill the two raw-SQL purges into db_write_log — HONESTLY labelled.

Context: two purges were executed via raw SQL, bypassing db_writer.py, so they left no
audit trail. Giles approved backfilling them provided each entry is clearly marked as a
BACKFILL (real execution time, explicit label, backup table named).

Entries written (action='backfill_delete', source='backfill_raw_sql_purge'):
  1. 2026-09-21 10:17:33  qc_issues  -9,070 rows  (completed + orphan PO-log rows)
     backup: qc_issues_backup_20260921_101733
  2. 2026-09-23 12:47:38  qc_issues  -5 rows      (no-QC-STATUS process notes)
     backup: qc_issues_pre_notepurge_20260923_124738

These are RECORDS OF PAST ACTION, not new deletions. Nothing is deleted by this script.
It writes exactly 2 rows to db_write_log.

Usage:
    python _tmp_backfill_log.py --dry-run
    python _tmp_backfill_log.py --commit
"""
import argparse, sqlite3, sys
from datetime import datetime, timezone
from pathlib import Path

DB = Path(r"C:\Users\GilesHugo\SES_Workspace\db\ops.db")

ENTRIES = [
    {
        "row_gid": "BACKFILL:qc_issues_purge_20260921",
        "fields_changed": ("BACKFILL DELETE -9,070 rows purged via raw SQL (bypassed db_writer). "
                           "Rows were completed PO-log rows + 8 orphans mirrored from the WRONG project "
                           "902269769783650 before the Stage 0 repoint. "
                           "backup_table=qc_issues_backup_20260921_101733 (9,109 rows). "
                           "Recorded retroactively 2026-09-23; original execution 2026-09-21T10:17:33Z."),
        "written_at": "2026-09-21 10:17:33",
    },
    {
        "row_gid": "BACKFILL:qc_issues_purge_20260923",
        "fields_changed": ("BACKFILL DELETE -5 rows purged via raw SQL (bypassed db_writer). "
                           "Rows were OPEN tasks with no QC STATUS (supplier process/reference notes: "
                           "NCR-3116, 3117, 3120, 3707, 4102) removed when the QC STATUS scope filter "
                           "was introduced. backup_table=qc_issues_pre_notepurge_20260923_124738 (10 rows). "
                           "Recorded retroactively 2026-09-23; original execution 2026-09-23T12:47:38Z."),
        "written_at": "2026-09-23 12:47:38",
    },
]

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--commit", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    if a.commit == a.dry_run:
        print("Specify exactly one of --commit / --dry-run")
        sys.exit(2)

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM db_write_log WHERE table_name='qc_issues'")
    print("qc_issues log rows before:", cur.fetchone()[0])

    for e in ENTRIES:
        cur.execute("SELECT COUNT(*) FROM db_write_log WHERE row_gid=?", (e["row_gid"],))
        exists = cur.fetchone()[0]
        print(f"\n{e['row_gid']}")
        print(f"  already present: {exists}")
        print(f"  written_at     : {e['written_at']}")
        print(f"  note           : {e['fields_changed'][:110]}...")

    if a.dry_run:
        print("\nDRY RUN — nothing written.")
        conn.close()
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    written = 0
    for e in ENTRIES:
        cur.execute("SELECT COUNT(*) FROM db_write_log WHERE row_gid=?", (e["row_gid"],))
        if cur.fetchone()[0]:
            print(f"skip (already logged): {e['row_gid']}")
            continue
        cur.execute(
            """INSERT INTO db_write_log (table_name, row_gid, action, source, fields_changed, written_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("qc_issues", e["row_gid"], "backfill_delete", "backfill_raw_sql_purge",
             e["fields_changed"], e["written_at"]),
        )
        written += 1

    conn.commit()
    cur.execute("SELECT COUNT(*) FROM db_write_log WHERE table_name='qc_issues'")
    print(f"\nentries written      : {written}")
    print("qc_issues log rows now:", cur.fetchone()[0])
    cur.execute("SELECT action, COUNT(*) n FROM db_write_log WHERE table_name='qc_issues' GROUP BY action")
    for r in cur.fetchall():
        print(f"   {r[0]:18s} {r[1]}")
    conn.close()
    print("\nBACKFILLED.")


if __name__ == "__main__":
    main()
