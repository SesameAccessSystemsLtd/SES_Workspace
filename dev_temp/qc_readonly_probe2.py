"""READ-ONLY probe #2 (Claude review, 2026-09-23). Opens ops.db mode=ro — cannot write.
Tests whether the Stage 1 gaps (PO link, supplier email, EDD) can be resolved from data
already in ops.db via SQR and part number. Output: dev_temp/qc_probe2_out.json
"""
import json, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "db" / "ops.db"
OUT = Path(__file__).resolve().parent / "qc_probe2_out.json"
conn = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

def rows(sql, args=()):
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except Exception as e:
        return f"ERROR: {e}"

# NCR-5040 and any other in-scope issue not yet mirrored: identify by the dry-run SQRs/parts too
issues = rows("SELECT asana_task_gid, ncr_id, sesame_part_no, vendor, associated_sqr_number, qc_status FROM qc_issues")
extra = [{"ncr_id": "NCR-5040", "sesame_part_no": "1597.7110.B.004", "vendor": "Laser Cut", "associated_sqr_number": None}]
res = {"issues": issues, "probes": []}

for it in (issues if isinstance(issues, list) else []) + extra:
    sqr, pn, vendor = it.get("associated_sqr_number"), it.get("sesame_part_no"), it.get("vendor")
    p = {"ncr": it.get("ncr_id"), "sqr": sqr, "part": pn, "vendor": vendor}
    if sqr:
        p["procurement_by_sqr"] = rows(
            """SELECT asana_task_gid, task_name, document_type, po_number, sqr_number, vendor_name,
                      vendor_gid, order_status, due_date, days_for_delivery, is_completed, project_code
               FROM procurement_tasks WHERE sqr_number = ? OR task_name LIKE ?""", (sqr, f"%{sqr}%"))
    if pn:
        p["parts_control_by_pn"] = rows(
            """SELECT asana_task_gid, sesame_pn, part_name, vendor_name, procurement_task_gid, po_task_gid,
                      order_status, due_on, is_completed, association_status, supply_route, project_code
               FROM parts_control WHERE sesame_pn = ? OR sesame_pn LIKE ?""", (pn, f"{pn}%"))
        p["po_lines_by_pn"] = rows("SELECT * FROM po_lines WHERE sesame_part_no = ?", (pn,))
    # resolve real supplier from any linked procurement task, then its email
    vend_names = set()
    for key in ("procurement_by_sqr", "parts_control_by_pn"):
        for r in p.get(key, []) if isinstance(p.get(key), list) else []:
            if r.get("vendor_name"):
                vend_names.add(r["vendor_name"])
    if vendor:
        vend_names.add(vendor)
    p["supplier_lookup"] = {}
    for v in vend_names:
        p["supplier_lookup"][v] = {
            "suppliers": rows("""SELECT vendor_name, contact_email, supplier_type, asana_task_gid
                                 FROM suppliers WHERE vendor_name = ? OR vendor_name_normalised = lower(?)
                                 OR vendor_name LIKE ? LIMIT 5""", (v, v, f"%{v}%")),
            "contacts": rows("""SELECT supplier_name, contact_name, email, is_primary
                                FROM supplier_contacts WHERE supplier_name LIKE ? LIMIT 5""", (f"%{v}%",)),
        }
    res["probes"].append(p)

# Coverage stats that matter for Stage 1
res["procurement_sqr_coverage"] = rows(
    "SELECT COUNT(*) n, SUM(sqr_number IS NOT NULL AND sqr_number!='') with_sqr, "
    "SUM(po_number IS NOT NULL AND po_number!='') with_po FROM procurement_tasks")
res["parts_control_link_coverage"] = rows(
    "SELECT COUNT(*) n, SUM(procurement_task_gid IS NOT NULL AND procurement_task_gid!='') with_proc, "
    "SUM(po_task_gid IS NOT NULL AND po_task_gid!='') with_po_task FROM parts_control")
res["process_vendors_in_suppliers"] = rows(
    "SELECT vendor_name, contact_email, supplier_type FROM suppliers "
    "WHERE vendor_name LIKE '%laser%' OR vendor_name LIKE '%powder%' OR vendor_name LIKE '%engrav%' LIMIT 20")
res["recent_qc_write_log_actions"] = rows(
    "SELECT action, COUNT(*) n, MAX(written_at) last FROM db_write_log WHERE table_name='qc_issues' GROUP BY action")
conn.close()
OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
print("WROTE", OUT)
