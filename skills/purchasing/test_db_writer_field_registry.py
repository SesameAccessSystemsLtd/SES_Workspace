r"""test_db_writer_field_registry.py — asana_field_registry seed text + db_writer.update_field_registry_notes.
Temp ops.db built by db_init_ops.init_db: no network, never touches the live ops.db.
Regression for 2026-09-25: the order_status note said 'Confirmed enum GID: 1143955334207043',
which is the '⏰On Order - Awaiting CONFIRMATION' option; Confirmed is 1203944084697376.
Run:  ..\..\.venv\Scripts\python.exe test_db_writer_field_registry.py"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

TMP = Path(tempfile.mkdtemp(prefix="field_registry_test_"))
DB = str(TMP / "ops.db")
os.environ["OPENCLAW_OPS_DB_PATH"] = DB  # db_writer resolves its default path at import
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "sql_db" / "scripts"))
import db_init_ops  # noqa: E402

db_init_ops.init_db(DB)
import db_writer  # noqa: E402  (after the DB exists — it refuses a missing file)

FIELD = "1143955334207040"
AWAITING = "1143955334207043"
CONFIRMED = "1203944084697376"
OLD_NOTE = f"ORDER Status field. Confirmed enum GID: {AWAITING}"


def eq(a, b, label):
    assert a == b, f"{label}\n  got : {a!r}\n  want: {b!r}"
    print(f"  ok  {label}")


def note(field_name):
    with sqlite3.connect(DB) as c:
        row = c.execute("SELECT notes FROM asana_field_registry WHERE field_name = ?", (field_name,)).fetchone()
    return row[0] if row else None


def registry_log():
    with sqlite3.connect(DB) as c:
        return c.execute("SELECT row_gid, action, source, fields_changed FROM db_write_log "
                         "WHERE table_name = 'asana_field_registry' ORDER BY id").fetchall()


seed = note("order_status")
eq(f"Delivery Date CONFIRMED = {CONFIRMED}" in seed, True, "seed: names 1203944084697376 as Delivery Date CONFIRMED")
eq(f'{AWAITING} is "On Order - Awaiting CONFIRMATION", NOT confirmed' in seed, True,
   "seed: names 1143955334207043 as Awaiting CONFIRMATION")
eq(OLD_NOTE in seed, False, "seed: old wrong claim gone")

# Simulate a database seeded before the fix (INSERT OR IGNORE never rewrites it).
with sqlite3.connect(DB) as c:
    c.execute("UPDATE asana_field_registry SET notes = ? WHERE field_name = 'order_status'", (OLD_NOTE,))

eq(db_writer.update_field_registry_notes("order_status", seed, source="test"),
   {"written": 1, "skipped": 0, "errors": 0}, "update: stale note corrected")
eq(note("order_status"), seed, "update: live note now equals the seed")
eq(registry_log(), [(FIELD, "update", "test", json.dumps(["notes"]))],
   "update: one db_write_log row, row_gid = field GID, fields_changed = notes")

eq(db_writer.update_field_registry_notes("order_status", seed, source="test"),
   {"written": 0, "skipped": 1, "errors": 0}, "update: identical note skipped")
eq(db_writer.update_field_registry_notes("no_such_field", "x", source="test"),
   {"written": 0, "skipped": 1, "errors": 0}, "update: unknown field skipped, no row created")
eq(note("no_such_field"), None, "update: no phantom row")
eq(len(registry_log()), 1, "update: skips are not logged as writes")

for args, label in ((("", "x"), "empty field_name"), (("order_status", ""), "empty notes")):
    try:
        db_writer.update_field_registry_notes(*args)
    except ValueError:
        print(f"  ok  update: {label} -> ValueError")
    else:
        raise AssertionError(f"update: {label} accepted")

print("ALL OK")
