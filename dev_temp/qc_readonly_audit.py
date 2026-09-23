"""READ-ONLY audit of ops.db:qc_issues and related tables (Claude review, 2026-09-23).
Opens the DB with mode=ro — cannot write. Output: dev_temp/qc_audit_out.json
"""
import json, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db" / "ops.db"
OUT = Path(__file__).resolve().parent / "qc_audit_out.json"
conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
res = {}

def q(name, sql, args=()):
    try:
        res[name] = [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception as e:
        res[name] = f"ERROR: {e}"

q("tables_like_qc", "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%qc%'")
q("qc_schema", "PRAGMA table_info(qc_issues)")
q("qc_indexes", "PRAGMA index_list(qc_issues)")
q("qc_rows", """SELECT asana_task_gid, ncr_id, issue_name, sesame_part_no, build_pack_reference,
    po_task_gid, associated_po_number, vendor, supplier_email, associated_sqr_number, qc_status,
    order_status, build_impact, needed_by, date_raised, completed, asana_modified_at, last_synced_at,
    created_at FROM qc_issues ORDER BY ncr_id""")
q("qc_counts", """SELECT COUNT(*) n, SUM(completed) completed, SUM(qc_status IS NULL OR qc_status='') no_status,
    COUNT(DISTINCT ncr_id) distinct_ncr, SUM(ncr_id IS NULL) null_ncr,
    SUM(qc_issue_reason='') empty_str_reason, SUM(needs_from_purch='') empty_str_needs,
    MIN(last_synced_at) min_sync, MAX(last_synced_at) max_sync FROM qc_issues""")
q("backup_counts", """SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'qc_issues_%'""")
for t in [r["name"] for r in res.get("backup_counts", []) if isinstance(r, dict)]:
    q(f"count_{t}", f"SELECT COUNT(*) n FROM [{t}]")
q("write_log_qc", """SELECT * FROM db_write_log WHERE table_name='qc_issues' ORDER BY rowid DESC LIMIT 40""")
q("field_registry", "SELECT * FROM asana_field_registry")
for t in ["purchase_orders", "po_lines", "supplier_deliveries", "supplier_contacts", "suppliers",
          "overdue_chase_log", "procurement_tasks", "parts_control", "part_supply_events"]:
    q(f"count_{t}", f"SELECT COUNT(*) n FROM [{t}]")
q("parts_control_cols", "PRAGMA table_info(parts_control)")
q("procurement_cols", "PRAGMA table_info(procurement_tasks)")
q("overdue_chase_log_cols", "PRAGMA table_info(overdue_chase_log)")
q("suppliers_cols", "PRAGMA table_info(suppliers)")
q("po_lines_cols", "PRAGMA table_info(po_lines)")
q("supplier_contacts_cols", "PRAGMA table_info(supplier_contacts)")
conn.close()

# Part -> parts_control / procurement joins for each open QC row (schema-agnostic probe)
conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row
pc_cols = {r[1] for r in conn.execute("PRAGMA table_info(parts_control)")}
part_col = next((c for c in ["sesame_part_no", "part_number", "part_no", "name"] if c in pc_cols), None)
res["parts_control_part_col"] = part_col
probe = []
for r in res["qc_rows"] if isinstance(res["qc_rows"], list) else []:
    pn = r.get("sesame_part_no")
    entry = {"ncr": r["ncr_id"], "part": pn, "vendor": r["vendor"]}
    if pn and part_col:
        try:
            entry["parts_control"] = [dict(x) for x in conn.execute(
                f"SELECT * FROM parts_control WHERE [{part_col}] LIKE ? LIMIT 5", (f"%{pn}%",))]
        except Exception as e:
            entry["parts_control"] = f"ERROR {e}"
    if r.get("vendor"):
        for t, c in [("suppliers", "vendor_name"), ("supplier_contacts", "contact_name")]:
            try:
                entry[f"{t}_match"] = [dict(x) for x in conn.execute(
                    f"SELECT * FROM {t} WHERE [{c}] LIKE ? LIMIT 5", (f"%{r['vendor']}%",))]
            except Exception as e:
                entry[f"{t}_match"] = f"ERROR {e}"
    probe.append(entry)
res["chain_probe"] = probe
OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
print("WROTE", OUT)
