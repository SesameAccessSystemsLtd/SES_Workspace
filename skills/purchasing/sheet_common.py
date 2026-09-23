"""
sheet_common.py — the Email Gateway Google Sheet, addressed by HEADER NAME.

Why: the Processed tab drifted (Outcome landed in column N on old rows and P
on new ones) because writers assumed positions. Every read/write here goes
through the header row, and missing columns are appended once, never reordered.

Tabs
  Pending   — awaiting Giles' decision (A / M / R in DECISION)
  Processed — full history, one row per decided email
  Rules     — the learned rules, human-readable (learn.py writes; Giles may edit)
"""
from __future__ import annotations

import os
import sys

SERVICE_ACCOUNT_PATH = r"C:\Users\GilesHugo\.hermes\Google_Service_Account.json"
SHEET_ID = "1fvoSKnvH0vdM_aZbo2_seY6AU3SZaXABaxRUKSxWVlo"
PENDING_TAB = "Pending"
PROCESSED_TAB = "Processed"
RULES_TAB = "RULES"          # Giles created it upper-case; lookup is case-insensitive anyway

# Column order for NEW sheets / newly appended columns. Existing sheets keep their
# order; anything here that is missing is appended to the right.
PENDING_HEADERS = [
    "Email ID", "Received", "Subject", "Sender Domain", "Document Type", "PO Number",
    "Matched Task", "Asana Link", "Proposed Action", "Comments", "DECISION", "Amendment Notes",
    "Processed At", "Response Needed", "Response Status",
    # 2026-09-18 additions
    "Triage", "Supplier", "Confidence", "Suggested", "Why", "Summary", "Action Required",
    "Alternatives", "Feedback", "Proposed Task GID", "Match Reasons", "Sender",
]
PROCESSED_EXTRA = ["Outcome", "Request ID", "Request Status", "Final Task GID", "Learned"]
PROCESSED_HEADERS = PENDING_HEADERS + PROCESSED_EXTRA
RULES_HEADERS = ["Type", "Key", "Maps To", "Note", "Learned From", "Learned At", "Active"]


def get_spreadsheet():
    """Open the Email Gateway sheet. Raises RuntimeError (never sys.exit) so a caller that
    treats the sheet as optional — learn.py publishing rules — can carry on."""
    try:
        import gspread
    except ImportError:
        raise RuntimeError("gspread not installed. Fix: .venv\\Scripts\\pip.exe install gspread")
    if not os.path.exists(SERVICE_ACCOUNT_PATH):
        raise RuntimeError(f"Google credentials not found at {SERVICE_ACCOUNT_PATH} (see GOOGLE_SHEETS_SETUP.md)")
    gc = gspread.service_account(filename=SERVICE_ACCOUNT_PATH)
    return gc.open_by_key(SHEET_ID)


class Tab:
    """A worksheet with a header map. `rows` are dicts keyed by header."""

    def __init__(self, spreadsheet, name: str, wanted_headers: list[str]):
        self.ws = None
        for ws in spreadsheet.worksheets():          # case-insensitive: 'Invoices' == 'INVOICES'
            if ws.title.strip().lower() == name.strip().lower():
                self.ws = ws
                break
        if self.ws is None:
            self.ws = spreadsheet.add_worksheet(title=name, rows=2000, cols=max(30, len(wanted_headers)))
            self.ws.append_row(wanted_headers, value_input_option="RAW")
        self.name = name
        self.header = [h.strip() for h in self.ws.row_values(1)]
        self._fix_legacy_blank_headers()
        self.ensure_columns(wanted_headers)

    # 04_sheet_writer (Aug 2026) wrote Response Needed / Response Status into N/O of the
    # Pending tab without ever naming those header cells. Name them once so the data
    # underneath is addressable instead of orphaned behind two blank headers.
    LEGACY_BLANKS = {"Pending": {13: "Response Needed", 14: "Response Status"}}

    def _fix_legacy_blank_headers(self) -> None:
        fixes = self.LEGACY_BLANKS.get(self.name, {})
        changed = False
        for idx, name in fixes.items():
            if idx < len(self.header) and self.header[idx] == "" and name not in self.header:
                self.header[idx] = name
                changed = True
        if changed:
            self.ws.update("1:1", [self.header], value_input_option="RAW")
            print(f"  [{self.name}] named legacy blank header cells: {', '.join(fixes.values())}")

    def ensure_columns(self, wanted: list[str]) -> list[str]:
        """Append any missing header names to the right of the existing header row."""
        missing = [h for h in wanted if h not in self.header]
        if not missing:
            return []
        new_header = self.header + missing
        if self.ws.col_count < len(new_header):
            self.ws.add_cols(len(new_header) - self.ws.col_count)
        self.ws.update("1:1", [new_header], value_input_option="RAW")
        self.header = new_header
        print(f"  [{self.name}] added columns: {', '.join(missing)}")
        return missing

    def col(self, name: str) -> int:
        """1-based column index."""
        return self.header.index(name) + 1

    def read_rows(self) -> list[dict]:
        """All data rows as dicts, each with '_row' = 1-based sheet row. Blank rows kept
        (as empty dicts) so row numbers stay honest."""
        vals = self.ws.get_all_values()
        out = []
        for i, r in enumerate(vals[1:], start=2):
            r = list(r) + [""] * (len(self.header) - len(r))
            d = {h: (r[j] if j < len(r) else "").replace("\u00a0", " ").strip() for j, h in enumerate(self.header)}
            d["_row"] = i
            out.append(d)
        return out

    def to_list(self, row: dict) -> list:
        return [("" if row.get(h) is None else str(row.get(h, ""))) for h in self.header]

    def append_rows(self, rows: list[dict]) -> int:
        if not rows:
            return 0
        self.ws.append_rows([self.to_list(r) for r in rows], value_input_option="RAW",
                            table_range="A1")
        return len(rows)

    def existing_ids(self, key: str = "Email ID") -> set[str]:
        try:
            return {v.strip() for v in self.ws.col_values(self.col(key))[1:] if v.strip()}
        except Exception:
            return set()

    def delete_rows(self, row_numbers: list[int]) -> None:
        for r in sorted(set(row_numbers), reverse=True):
            self.ws.delete_rows(r)

    def replace_all(self, rows: list[dict]) -> None:
        """Rewrite the whole tab (used for the Rules tab)."""
        self.ws.clear()
        self.ws.update("1:1", [self.header], value_input_option="RAW")
        if rows:
            self.ws.append_rows([self.to_list(r) for r in rows], value_input_option="RAW", table_range="A1")
