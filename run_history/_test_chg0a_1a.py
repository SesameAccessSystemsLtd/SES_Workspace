"""Scratch test for CHG-0A + CHG-1A. Exercises a TEMP SQLite DB only — never live ops.db."""
import os
import sys
import sqlite3
import tempfile
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
sys.path.insert(0, str(WS / "skills" / "os_portal"))
sys.path.insert(0, str(WS / "skills" / "purchasing"))

# ---- CHG-0A ----
from centre_of_truth import reconcile_ready, render_pref, safe_api_get, AsanaUnavailable

rows = [{"gid": "a", "status": "DB-A"}, {"gid": "b", "status": "DB-B"}]

# token_h None -> all (row, None), no live fetch
assert reconcile_ready(rows, lambda r: {"completed": False}, key="gid", token_h=None) == [(r, None) for r in rows]

# live fetch returns completed for 'a' -> caller drops it (reconcile_ready does NOT drop)
prep = reconcile_ready(rows, lambda r: {"completed": (r["gid"] == "a")}, key="gid", token_h={"Authorization": "x"}, cap=10)
assert prep[0][0]["gid"] == "a" and prep[0][1] == {"completed": True}
assert prep[1][1] == {"completed": False}

# render_pref live-first + fallback + flag
assert render_pref("LIVE", "DB") == ("LIVE", False)
assert render_pref(None, "DB") == ("DB", True)
assert render_pref("", "DB") == ("DB", True)

# safe_api_get returns None on failure
assert safe_api_get(lambda: (_ for _ in ()).throw(AsanaUnavailable("boom"))) is None
assert safe_api_get(lambda: "ok") == "ok"
print("CHG-0A: OK (reconcile_ready / render_pref / safe_api_get / AsanaUnavailable)")

# ---- CHG-1A ----
from db_writer import update_parts_control_stock_action

tmpdir = Path(tempfile.mkdtemp(prefix="chg1a_"))
tmpdb = tmpdir / "ops_test.db"
conn = sqlite3.connect(str(tmpdb))
conn.execute("""CREATE TABLE parts_control (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    asana_task_gid TEXT UNIQUE NOT NULL,
    sesame_pn TEXT, part_name TEXT NOT NULL, order_qty REAL, stock_action TEXT,
    vendor_name TEXT, build_assembly_pack_gid TEXT, procurement_task_gid TEXT,
    is_subtask INTEGER NOT NULL DEFAULT 0, project_name TEXT, last_synced_at TEXT,
    created_at TEXT DEFAULT (datetime('now')), is_completed INTEGER NOT NULL DEFAULT 0,
    vendor_name_normalised TEXT, association_status TEXT, association_checked_at TEXT)""")
conn.execute("""CREATE TABLE db_write_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL, row_gid TEXT,
    action TEXT NOT NULL, source TEXT NOT NULL, fields_changed TEXT,
    written_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)""")
conn.execute("INSERT INTO parts_control (asana_task_gid, part_name, stock_action) VALUES ('test_gid_1','Test Part','SUBCON TASKS')")
conn.commit()
conn.close()

r = update_parts_control_stock_action("test_gid_1", "On order (dependents NOT ORDER TASKS)", source="test", db_path=str(tmpdb))
assert r == {"written": 1, "skipped": 0, "errors": 0}, r

c = sqlite3.connect(str(tmpdb))
c.row_factory = sqlite3.Row
sa = c.execute("SELECT stock_action FROM parts_control WHERE asana_task_gid='test_gid_1'").fetchone()["stock_action"]
logrow = c.execute("SELECT table_name, row_gid, action, source FROM db_write_log WHERE table_name='parts_control'").fetchone()
c.close()
assert sa == "On order (dependents NOT ORDER TASKS)", sa
assert logrow is not None and logrow["source"] == "test" and logrow["action"] == "update", logrow

# idempotent second run on same gid still writes (targeted UPDATE, rowcount=1)
r2 = update_parts_control_stock_action("test_gid_1", "On order (dependents NOT ORDER TASKS)", source="test", db_path=str(tmpdb))
assert r2["written"] == 1

# unknown gid -> skipped, not error
r3 = update_parts_control_stock_action("no_such_gid", "X", source="test", db_path=str(tmpdb))
assert r3 == {"written": 0, "skipped": 1, "errors": 0}, r3
print("CHG-1A: OK (update_parts_control_stock_action writes stock_action + db_write_log, idempotent, safe skip)")
print("temp db:", tmpdb)
