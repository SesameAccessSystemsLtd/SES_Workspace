"""Build a verification workbook: photo-read values vs live Asana, for Giles to check."""
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

WS = Path(r"C:\Users\GilesHugo\SES_Workspace")
from dotenv import load_dotenv
load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)
import requests
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

TOKEN=os.getenv("ASANA_PERSONAL_ACCESS_TOKEN", "")
H = {"Authorization": "Bearer " + TOKEN}
STOCK_GID = "1126782469949969"   # STOCK QTY (number) — verified live 2026-11-03

db = sqlite3.connect(str(WS / "db" / "stock_audit.db"))
db.row_factory = sqlite3.Row

rows = {}
for r in db.execute(
    "SELECT sheet_id,row_key,status,count_qty,evidence_photo,ingested_at "
    "FROM count_result WHERE source='photo' ORDER BY ingested_at"
):
    d = dict(r)
    rows[(d["sheet_id"], d["row_key"])] = d

wb = {}
for r in db.execute(
    "SELECT task_gid,outcome,live_before,target,request_id FROM writeback_result WHERE kind='line'"
):
    d = dict(r)
    wb[str(d["task_gid"])] = d

cache = {}


def asana(gid):
    gid = str(gid)
    if gid in cache:
        return cache[gid]
    try:
        r = requests.get(
            "https://app.asana.com/api/1.0/tasks/" + gid,
            headers=H,
            params={"opt_fields": "name,custom_fields.gid,custom_fields.number_value"},
            timeout=25,
        )
        d = r.json().get("data") or {}
        qty = None
        for cf in d.get("custom_fields") or []:
            if cf.get("gid") == STOCK_GID:
                qty = cf.get("number_value")
        out = (d.get("name") or "", qty)
    except Exception:
        out = ("", None)
    cache[gid] = out
    return out


wbk = Workbook()
ws = wbk.active
ws.title = "Photo Read Verification"

TITLE = Font(bold=True, size=14)
HDR = Font(bold=True, color="FFFFFF")
HFILL = PatternFill("solid", fgColor="1F4E79")
ORANGE = PatternFill("solid", fgColor="FFC000")
GREEN = PatternFill("solid", fgColor="C6EFCE")
thin = Side(style="thin", color="BFBFBF")
BORDER = Border(left=thin, right=thin, top=thin, bottom=thin)

ws["A1"] = "Stock Count Photo Read - Verification Sheet"
ws["A1"].font = TITLE
ws["A2"] = (
    "Generated %s.  Each row = one hand-written count read from a photograph. "
    "Check the Photo COUNT column against the photo named in the evidence column."
    % datetime.now().strftime("%d %b %Y %H:%M")
)
ws["A2"].font = Font(italic=True, size=9)
ws["A3"] = (
    "Green = read clean and written to Asana.  Amber = reader flagged it unclear "
    "(HELD BACK, never written) - needs a human eye on the photo."
)
ws["A3"].font = Font(italic=True, size=9, color="C00000")

headers = [
    "Sheet", "Task GID", "Part name (live Asana)", "Photo COUNT", "Reader status",
    "Written to Asana?", "Live qty now", "Photo evidence", "Check",
]
ws.append([])
ws.append(headers)
hr = ws.max_row
for c in range(1, len(headers) + 1):
    cell = ws.cell(row=hr, column=c)
    cell.font = HDR
    cell.fill = HFILL
    cell.border = BORDER
    cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

n_clean = 0
n_flag = 0
for (sheet, row_key), d in sorted(rows.items()):
    if d["status"] == "blank":
        continue
    gid = str(d.get("task_gid") or row_key.split(":")[0])
    name, live = asana(gid)
    w = wb.get(gid)
    written = "-"
    if w:
        if w["outcome"] == "written":
            written = "YES (%s)" % str(w["request_id"]).split("-")[-1]
        else:
            written = str(w["outcome"])
    status = d["status"]
    if status == "counted":
        n_clean += 1
        verdict, fill = "Clean - verify count matches photo", GREEN
    else:
        n_flag += 1
        verdict, fill = "FLAGGED - reader unsure, check photo", ORANGE
    ws.append([
        sheet,
        str(row_key).split(":")[0],
        str(name)[:62],
        d["count_qty"],
        status,
        written,
        live,
        (d["evidence_photo"] or "")[:12],
        verdict,
    ])
    r = ws.max_row
    for c in range(1, len(headers) + 1):
        cell = ws.cell(row=r, column=c)
        cell.border = BORDER
        cell.alignment = Alignment(vertical="top", wrap_text=(c == 3))
    ws.cell(row=r, column=9).fill = fill
    ws.cell(row=r, column=4).fill = GREEN if status == "counted" else ORANGE

ws.append([])
s = ws.max_row + 1
ws.cell(row=s, column=1, value="SUMMARY").font = Font(bold=True, size=12)
labels = [
    ("Rows read clean and eligible for write-back", n_clean),
    ("Rows flagged for human check (held back)", n_flag),
    ("Clean rate", ("%.1f%%" % ((n_clean / (n_clean + n_flag)) * 100)) if (n_clean + n_flag) else "n/a"),
    ("Total photo rows reviewed", n_clean + n_flag),
]
for i, (lab, val) in enumerate(labels, 1):
    ws.cell(row=s + i, column=1, value=lab)
    ws.cell(row=s + i, column=4, value=val).font = Font(bold=True)

widths = [19, 8, 64, 13, 14, 20, 13, 15, 40]
for i, wd in enumerate(widths, 1):
    ws.column_dimensions[get_column_letter(i)].width = wd
ws.freeze_panes = "A%d" % (hr + 1)
ws.auto_filter.ref = "A%d:I%d" % (hr, ws.max_row)

out = WS / "data" / "stock_audit" / "STOCK-COUNT-PHOTO-VERIFICATION-2026-11-03.xlsx"
wbk.save(str(out))
print("WROTE " + str(out))
print("clean=%d flagged=%d total=%d" % (n_clean, n_flag, n_clean + n_flag))
