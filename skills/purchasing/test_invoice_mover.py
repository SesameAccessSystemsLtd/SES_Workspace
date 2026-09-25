"""test_invoice_mover.py — unit tests for 07's pure helpers (no Dropbox, no Asana, no ops.db).
Run:  ..\..\.venv\Scripts\python.exe test_invoice_mover.py"""
import re, sys, types
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# stubs so 07 imports without the live estate
stub = types.ModuleType("06_invoice_processor")
stub.DB_PATH = ""; stub.REQUESTS_DIR = ""; stub.FOLDER_AUTO = "AI SIGNED OFF"; stub.FOLDER_CHECK = "2. invoice queries"
stub.FOLDER_SIGNED = "3. INVOICES - Signed Off"; stub.INVOICE_BASE = Path("."); stub.INVOICE_BASE_REL = "x"; stub.FILING_ENABLED = False
stub.safe_filename = lambda s: re.sub(r'[\\/:*?"<>|]+', "_", s).strip()
stub.dropbox_web_url = lambda folder, name: f"https://dropbox/{name}"
sys.modules["06_invoice_processor"] = stub
gc = types.ModuleType("gateway_common"); gc.GILES_EMAIL = "x"; gc.now_utc = lambda: "2026-09-24T00:00:00Z"
gc.append_jsonl = lambda *a, **k: None; gc.DATA = Path("."); gc.shadow_mode = lambda: True
sys.modules["gateway_common"] = gc
m = __import__("07_invoice_mover")

def eq(a, b, label):
    assert a == b, f"{label}\n  got : {a!r}\n  want: {b!r}"
    print(f"  ok  {label}")

# invoice_list — union across sources, dedupe, keep order
eq(m.invoice_list("00626763 / 00626764", "00626765|00626763", None), ["00626763", "00626764", "00626765"], "invoice_list union/dedupe")
eq(m.invoice_list("12936929 / 12940672"), ["12936929", "12940672"], "invoice_list slash")

# title_base — strips trailing invoice segments only when they are known invoices
eq(m.title_base("21636-1597 - Goodman - Ferrous BOM  1597 - Forrest Road - 00626765", ["00626763", "00626764", "00626765"]),
   "21636-1597 - Goodman - Ferrous BOM  1597 - Forrest Road", "title_base strips last-invoice suffix")
eq(m.title_base("21581-270826 - Orbital - Studding Jenners and drill bits - 12936929 / 12940672", ["12936929", "12940672"]),
   "21581-270826 - Orbital - Studding Jenners and drill bits", "title_base strips slash-list suffix")
eq(m.title_base("21640-1597 - Magnet Schultz - 1597 - Forrest Road Entire System - ", ["90007765"]),
   "21640-1597 - Magnet Schultz - 1597 - Forrest Road Entire System", "title_base trims empty trailing slot")
eq(m.title_base("21617-1597 - IGUS - 1597 - Forrest Road Entire System", ["0466716"]),
   "21617-1597 - IGUS - 1597 - Forrest Road Entire System", "title_base leaves description alone (1597 is not an invoice)")

# title_with_invoices — the rule Giles asked about
eq(m.title_with_invoices("21636-1597 - Goodman - Ferrous BOM  1597 - Forrest Road - 00626765", ["00626763", "00626764", "00626765"]),
   "21636-1597 - Goodman - Ferrous BOM  1597 - Forrest Road - 00626763 / 00626764 / 00626765", "title: multi-invoice slash list, old suffix folded in")
eq(m.title_with_invoices("21658-170826 - keyence - distance sensor cable ", ["1000347421"]),
   "21658-170826 - keyence - distance sensor cable - 1000347421", "title: single invoice appended")
eq(m.title_with_invoices("21627-1600 - Underwoods - PLC Components  (Subtotal: £741.74)", ["01631046", "01631052"]),
   "21627-1600 - Underwoods - PLC Components  (Subtotal: £741.74) - 01631046 / 01631052", "title: two invoices, description with brackets kept")

# filed_name_for — one file per invoice, convention name
eq(m.filed_name_for("21636-1597 - Goodman - Ferrous BOM  1597 - Forrest Road - 00626765", "00626763", ["00626763", "00626764", "00626765"]),
   "21636-1597 - Goodman - Ferrous BOM  1597 - Forrest Road - 00626763.pdf", "filed_name per invoice")
eq(m.filed_name_for("21640-1597 - Magnet Schultz - 1597 - Forrest Road Entire System - ", "90007765", ["90007765"]),
   "21640-1597 - Magnet Schultz - 1597 - Forrest Road Entire System - 90007765.pdf", "filed_name from 'Invoice.pdf' drop")

# split_plan — pages assigned to the invoice whose number appears on/before them
pages = ["... INVOICE 22/09/2026 00626763 ...", "... INVOICE 22/09/2026 00626764 ...", "... INVOICE 22/09/2026 00626765 ..."]
eq(m.split_plan(pages, ["00626763", "00626764", "00626765"]), {"00626763": [0], "00626764": [1], "00626765": [2]}, "split_plan 3 invoices / 3 pages")
eq(m.split_plan(["cover page", "inv A123 p1", "p2 continued", "inv B456"], ["A123", "B456"]), None, "split_plan refuses when a page precedes the first invoice")
eq(m.split_plan(["inv A123 p1", "p2 continued", "inv B456"], ["A123", "B456"]), {"A123": [0, 1], "B456": [2]}, "split_plan continuation pages follow their invoice")
eq(m.split_plan(["inv A123"], ["A123", "B456"]), None, "split_plan refuses when an invoice is never found")
print("ALL OK")
