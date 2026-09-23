"""
test_gateway.py — offline regression test for the email gateway (no LLM, no sheet, no Asana).

Builds a throwaway ops.db, stubs the LLM, runs parse → match → propose, and asserts
the behaviours Giles asked for. Run after touching any gateway file:

    .venv\\Scripts\\python.exe skills\\purchasing\\test_gateway.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1] / "lib"))

TMP = Path(tempfile.mkdtemp(prefix="gateway_test_"))
os.environ["OPENCLAW_OPS_DB_PATH"] = str(TMP / "ops.db")
os.environ["GATEWAY_RULES_PATH"] = str(TMP / "gateway_rules.json")

import gateway_common as gc  # noqa: E402

gc.RULES_PATH = TMP / "gateway_rules.json"
gc.ARCHIVE_DIR = TMP / "archive"
from matcher import Matcher  # noqa: E402
import importlib  # noqa: E402

pe = importlib.import_module("03_proposal_engine")

RC_BODY = """Notes
Dear Giles Hugo, Here’s the notes from your call with Amanda - Stephenson Gobin 01388 770360 on 22 June 2026 at 15:18:
Amanda confirmed the brake units for Forrest Road will ship on 25 September; Giles asked for the tracking reference by email.
Recap
Amanda from Stephenson Gobin confirmed dispatch of the two motor brake units on 25 September.
Giles asked for the tracking reference to be emailed.
Tasks
Email tracking reference to Giles.
↗ View transcript Powered by RingCentral AI Assistant"""


def build_db():
    c = sqlite3.connect(TMP / "ops.db")
    c.execute("""CREATE TABLE procurement_tasks(asana_task_gid TEXT PRIMARY KEY, task_name TEXT, order_status TEXT,
                 is_completed INTEGER, due_date TEXT, po_number TEXT, sqr_number TEXT, vendor_name TEXT,
                 price_ex_vat REAL, is_subtask INTEGER, last_synced_at TEXT, notes TEXT)""")
    rows = [
        ("1", "21615-1597 - MayTec - 1597 - Forrest Road Entire System", "🚚On Order - Delivery Date CONFIRMED", 0, "2026-09-23", "21615-1597", "SQR-46913", "MayTec", 1600.77, 0, "", ""),
        ("2", "21202-1569 - Machguard - Brooks Roller shutter", "✅ARRIVED - MOVES TO INVOICES!!", 1, "2026-08-01", "21202-1569", "", "Machguard", None, 0, "", ""),
        ("3", "21600-1569 - Machguard - Brooks roller blind replacement", "⏰On Order - Awaiting CONFIRMATION", 0, "2026-10-01", "21600-1569", "", "Machguard", None, 0, "", ""),
        ("4", "21650-1597 - SG transmission - 1597 Forrest Road motor brakes", "⏰On Order - Awaiting CONFIRMATION", 0, "2026-10-05", "21650-1597", "", "SG transmission", None, 0, "", ""),
        ("5", "21577-260826 - Midland Battery Services - Forklift battery", "⏰On Order - Awaiting CONFIRMATION", 0, "2026-10-05", "21577-260826", "", "Midland Battery", None, 0, "", ""),
        ("6", "21700-1580 - Warco - bench vice", "⏰On Order - Awaiting CONFIRMATION", 0, "2026-10-05", "21700-1580", "", "Warco", None, 0, "", "Web order 61894"),
    ]
    c.executemany("INSERT INTO procurement_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    c.execute("""CREATE TABLE db_write_log(id INTEGER PRIMARY KEY, table_name TEXT, row_gid TEXT, action TEXT,
                 source TEXT, fields_changed TEXT, written_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
    c.commit()


def check(cond, msg):
    print(("  PASS " if cond else "  FAIL ") + msg)
    return bool(cond)


def main():
    build_db()
    rules = gc.RuleStore(gc.RULES_PATH)
    sup = gc.SupplierDirectory(rules=rules)
    m = Matcher(suppliers=sup, rules=rules)
    ok = True

    # 1. SQR exact
    r = m.match(sender_email="tracey@appliedautomation.co.uk", sender_domain="appliedautomation.co.uk", subject="RE: 1597 - Forrest Road - SQR-46913 JMK32765",
                body_text="dispatched today", insights={"document_type": "dispatch_note", "delivery_date": "2026-09-18"})
    ok &= check(r.task and r.task.gid == "1" and r.confidence == "HIGH", "SQR in subject → exact HIGH match")
    acts, notes = pe.propose_changes({"document_type": "dispatch_note", "delivery_date": "2026-09-18", "_received": "2026-09-17T10:00:00Z"}, r.to_dict(), "dispatch_note")
    ok &= check("Update delivery date" not in " ".join(acts) and any("earlier than task due" in n for n in notes), "earlier delivery than due date → no date change, explained")
    ok &= check(not any("ORDER Status" in a for a in acts), "status already CONFIRMED → not re-proposed")

    # 2. closed task never auto-matched; open sibling picked via supplier + job
    r = m.match(sender_email="nick@machguard.co.uk", sender_domain="machguard.co.uk", subject="RE: Bellows - Langley Street",
                body_text="update on your 21202 order", insights={"document_type": "update", "job_numbers": ["1569"]})
    ok &= check(r.task and r.task.gid == "3", f"closed 21202 skipped, open Machguard job 1569 chosen ({r.matched_by})")
    ok &= check(any("skipped" in x for x in r.reasons), "skip is explained in reasons")

    # 3. arrived task + confirmation → no status change
    acts, notes = pe.propose_changes({"document_type": "order_confirmation", "delivery_date": "2026-09-30", "_received": ""},
                                     {"task_gid": "2", "task_status": "✅ARRIVED - MOVES TO INVOICES!!", "task_completed": True}, "order_confirmation")
    ok &= check(not acts and notes, "arrived task → no field change, note instead")

    # 4. RingCentral: label → supplier → single open order; summary/tasks parsed
    r = m.match(sender_email="service-uk@ringcentral.com", sender_domain="ringcentral.com",
                subject="Notes from your call with Amanda - Stephenson Gobin 01388 770360 on 22/06/2026 15:18", body_text=RC_BODY, insights={"document_type": "call_notes"})
    ok &= check(r.task and r.task.gid == "4", f"RC label → Stephenson Gobin → its open order ({r.matched_by})")
    rc = gc.parse_ringcentral("Notes from your call with Amanda - Stephenson Gobin 01388 770360 on 22/06/2026 15:18", RC_BODY)
    ok &= check(rc["tasks"] == ["Email tracking reference to Giles."] and rc["summary"].startswith("Amanda confirmed"), "RC summary + tasks parsed")

    # 5. whole-word supplier match: IDL must not match Midland
    r = m.match(sender_email="service-uk@ringcentral.com", sender_domain="ringcentral.com",
                subject="Notes from your call with Dave Burt IDL 07810 558753 on 18/09/2026 10:00", body_text="", insights={"document_type": "call_notes"})
    ok &= check(r.task is None, "IDL does not match 'Midland' by substring")

    # 6. supplier order ref found in task notes
    r = m.match(sender_email="orders@warco.co.uk", sender_domain="warco.co.uk", subject="Order #61894 confirmed", body_text="",
                insights={"document_type": "order_confirmation", "supplier_order_ref": "61894"})
    ok &= check(r.task and r.task.gid == "6", f"supplier order ref in task notes → match ({r.matched_by})")

    # 7. learning: a corrected link on an RC call teaches phone → task, then matches HIGH
    import learn
    learn.apply_feedback([{"email_id": "x", "received": "2026-09-18", "subject": "Notes from your call with Dave Burt IDL 07810 558753 on 18/09/2026 10:00",
                           "sender": "service-uk@ringcentral.com", "sender_domain": "ringcentral.com", "decision": "M",
                           "proposed_task_gid": "", "final_task_gid": "5", "amendment_notes": "", "feedback": "this is the forklift battery"}])
    rules2 = gc.RuleStore(gc.RULES_PATH)
    m2 = Matcher(suppliers=gc.SupplierDirectory(rules=rules2), rules=rules2)
    r = m2.match(sender_email="service-uk@ringcentral.com", sender_domain="ringcentral.com",
                 subject="Notes from your call with Dave Burt IDL 07810 558753 on 19/09/2026 10:00", body_text="", insights={"document_type": "call_notes"})
    ok &= check(r.task and r.task.gid == "5" and r.confidence == "HIGH", "after correction, same phone → learned task (HIGH)")

    # 8. learning: 'closed' feedback blocks the proposed task
    learn.apply_feedback([{"email_id": "y", "received": "2026-09-18", "subject": "x", "sender": "a@machguard.co.uk", "sender_domain": "machguard.co.uk",
                           "decision": "R", "proposed_task_gid": "3", "final_task_gid": "", "amendment_notes": "", "feedback": "wrong task - this is closed out"}])
    rules3 = gc.RuleStore(gc.RULES_PATH)
    ok &= check(rules3.is_blocked_task("3") is not None, "'closed out' feedback → blocked task rule")

    # 9. noise rule
    ok &= check(rules3.noise_reason("bookingplatform.apc-overnight.com", "x") is not None, "APC seeded as noise")

    # 10. comment has no HTML mention tag and leads with the summary
    item = {"subject": "Invoice", "receivedDateTime": "2026-09-17T13:44:45Z", "sender_domain": "appliedautomation.co.uk", "sender_name": "Tracey Spear"}
    ins = {"document_type": "invoice", "summary": "Applied Automation have invoiced the MayTec order.", "action_items": [], "supplier_invoice_ref": "INV-1"}
    txt = pe.build_comment(item, ins, {"supplier_name": "Applied Automation", "matched_by": "SQR-46913 (exact)"}, [], [])
    ok &= check("<a " not in txt and txt.splitlines()[0].startswith("Invoice from Applied Automation") and "Applied Automation have invoiced" in txt, "comment is plain text, summary-first")

    # 11. persistence: queue → email_documents + line items; then an invoice quoting the
    #     confirmation's order ref matches the same task from local history
    q = [{"email_id": "E1", "internet_message_id": "<e1@x>", "received_at": "2026-09-10T09:00:00Z", "sender": "orders@orbitalfasteners.co.uk",
          "sender_domain": "orbitalfasteners.co.uk", "subject": "Acknowledgement of order 000759386", "triage": "supplier_doc",
          "supplier_gid": "s1", "supplier_name": "Orbital", "asana_task_gid": "4", "asana_task_name": "x", "confidence": "HIGH",
          "matched_by": "PO", "alternatives": [], "action_required": False,
          "insights": {"document_type": "order_confirmation", "supplier_order_ref": "000759386", "total_price": 1234.5,
                       "line_items": [{"part_ref": "M8x30", "description": "bolt", "qty": 100, "unit_price": 0.12, "notes": None}]}}]
    msg = pe.persist_to_db(q)
    ok &= check("1 email_documents written" in msg and "1 line items" in msg, f"persisted to ops.db ({msg})")
    import db_writer
    db_writer.update_email_document_decision("E1", "A", "APPROVED", "4")
    con = sqlite3.connect(TMP / "ops.db")
    ok &= check(con.execute("SELECT line_total, task_gid FROM email_line_items").fetchone() == (12.0, "4"), "line total computed, line re-pointed to final task")
    m3 = Matcher(suppliers=sup, rules=rules)
    r = m3.match(sender_email="accounts@orbitalfasteners.co.uk", sender_domain="orbitalfasteners.co.uk", subject="Invoice 88812", body_text="",
                 insights={"document_type": "invoice", "supplier_order_ref": "000759386", "supplier_invoice_ref": "88812"})
    ok &= check(r.task and r.task.gid == "4" and "seen on order_confirmation" in r.matched_by, f"invoice matched via earlier confirmation ({r.matched_by})")

    # 12. invoice gate regression on the labelled signed-off invoices (history mode)
    import invoice_gate as ig
    recs = gc.load_json(HERE / "fixtures" / "signed_off_invoices.json", [])
    idx = ig.POIndex.from_records(recs)
    autos = 0
    for r in recs:
        hits = sup.by_email_domain(r["sender_domain"])
        key = gc.SupplierDirectory.short_name(hits[0]) if hits else (r["asana"].get("vendor") or r["sender_domain"])
        prof = ig.SupplierProfiles()
        for o in recs:
            if o is not r and o["sender_domain"] == r["sender_domain"]:
                prof.learn(key, o["asana"].get("invoice_number")); prof.learn_vendor(o["sender_domain"], o["asana"].get("vendor"))
        res = ig.run_gate(r["text"], r["sender_domain"], idx, prof, sup, file=r["file"])
        autos += res.auto
        if res.auto and r["asana"].get("invoice_number") and res.chosen_invoice not in r["asana"]["invoice_number"]:
            ok &= check(False, f"invoice gate chose {res.chosen_invoice} vs Asana {r['asana']['invoice_number']} on {r['file'][:40]}")
    ok &= check(autos == 7, f"invoice gate: {autos} AUTO of {len(recs)} labelled invoices (expected 7: proforma stopped, 4 no-PO, 1 multi-invoice)")
    ok &= check(any("PROFORMA" in h for h in ig.run_gate(recs[1]["text"], recs[1]["sender_domain"], idx, ig.SupplierProfiles(), sup).hard_stops), "proforma is a hard stop")

    print("\nALL PASS" if ok else "\nSOME CHECKS FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
