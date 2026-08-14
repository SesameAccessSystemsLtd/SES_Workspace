"""
Stage 4 -- Staging Export
=========================
Exports the stock-checked BOM (all rows) as a formatted Excel workbook for review.

Left-hand audit panel (first 12 columns, matches Giles review format):
  Override/Reorder QTY | Low Stock Alert | BOM QTY | BOM Description | ORDER_PICK |
  Remaining after BOM | Mapped KAN | Asana Task Name | Part Number | Stock QTY |
  min Stock QTY | Asana Task URL (hyperlink)

Then: Order reference | Order Status | cost center | vendor | material | ... BOM fields

Row colours: ORDER=red, PICK=green. Low Stock Alert: YES=red cell, NO=green cell.

Override / Reorder QTY column (Column A):
  SKIP         -- exclude from import entirely
  FORCE_ORDER  -- move to TO ORDER section regardless of stock status
  HOLD         -- treated as SKIP in Stage 5
  {number}     -- force reorder of this specific quantity (overrides BOM qty)

Mapped KAN column (Column G) is EDITABLE:
  If you correct or add a KAN number here, Stage 5 will use it AND add a new entry
  to the Golden Index CSV (stripping the project prefix from the part number).

Usage:
    python 04_staging_export.py --input bom_stock_check.csv
    python 04_staging_export.py --input bom_stock_check.csv --output My_Review.xlsx
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

# ORDER_PICK row colours
OP_FILL = {
    'ORDER': 'FFFFC7CE',   # red
    'PICK':  'FFC6EFCE',   # green
}
HEADER_FILL = PatternFill('solid', fgColor='FF1F4E79')
HEADER_FONT = Font(bold=True, color='FFFFFFFF')
THIN   = Side(style='thin', color='FFD9D9D9')
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# Columns to EXCLUDE from staging view (hidden — too noisy for review)
EXCLUDE_COLS = {'Asana Task GID', 'Asana Task Description', 'Stock Check Status',
                'Status', 'assembly pack name', 'assembly pack details',
                'BOM Description', 'Part Number', 'BOM QTY'}  # cleared after rename

# Column rename map: source column name -> display name in Excel
COL_RENAMES = {
    'description': 'BOM Description',
    'part number':  'Part Number',
    'item qty':     'BOM QTY',
    'Override':     'Override / Reorder QTY',
}

# Columns to show at front (in order) — MATCHES GILES REVIEW FORMAT
# Use the RENAMED names here where applicable
PRIORITY_COLS = [
    'Override / Reorder QTY',   # A: enter SKIP/HOLD/FORCE_ORDER or a number for reorder qty
    'Low Stock Alert',           # B: traffic light
    'BOM QTY',                   # C
    'BOM Description',           # D
    'vendor',                    # E: supplier — moved forward for quick audit
    'ORDER_PICK',                # F
    'Remaining after BOM',       # G
    'Mapped KAN',                # H: EDITABLE — enter/correct KAN number here
    'Asana Task Name',           # I
    'Part Number',               # J: full Sesame PN (e.g. 1575.1008.T.8ID_Grommet)
    'Matched Stock_Number',      # K: Rev4 Golden Index key matched against — verify this is correct
    'Stock QTY',                 # L
    'min Stock QTY',             # M
    'Asana Task URL',            # N: hyperlink
    'Order reference',           # N
    'Order Status',              # O
    'cost center', 'material', 'thickness', 'stock number',
    'web link', 'notes', 'mass', 'volume', 'source bom file', 'rev',
    'authority',
]

# Traffic light fills for Low Stock Alert
LOW_STOCK_FILL = {
    'YES': 'FFFFC7CE',   # red
    'NO':  'FFC6EFCE',   # green
}


def col_idx(header, name):
    """Find 1-based column index, case-insensitive. Returns None if not found."""
    for i, h in enumerate(header, 1):
        if str(h or '').strip().lower() == name.lower():
            return i
    return None


def apply_styles(ws, op_col_idx, low_stock_col_idx):
    """Header styles, ORDER/PICK row colours, Low Stock traffic lights, freeze pane, auto widths."""
    for cell in ws[1]:
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal='center', wrap_text=True)
        cell.border = BORDER
    ws.freeze_panes = 'A2'

    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        op_val = str(row[op_col_idx - 1].value or '').strip().upper() if op_col_idx else ''
        fill_hex = OP_FILL.get(op_val)
        row_fill = PatternFill('solid', fgColor=fill_hex) if fill_hex else None

        for cell in row:
            if row_fill:
                cell.fill = row_fill
            cell.border = BORDER
            cell.alignment = Alignment(wrap_text=False)

        # Override the Low Stock Alert cell with traffic light colour
        if low_stock_col_idx:
            ls_cell = row[low_stock_col_idx - 1]
            ls_val = str(ls_cell.value or '').strip().upper()
            ls_hex = LOW_STOCK_FILL.get(ls_val)
            if ls_hex:
                ls_cell.fill = PatternFill('solid', fgColor=ls_hex)
                ls_cell.font = Font(bold=True)

    for col_idx_w, col in enumerate(ws.columns, 1):
        max_len = max((len(str(cell.value or '')) for cell in col), default=0)
        ws.column_dimensions[get_column_letter(col_idx_w)].width = min(max(max_len + 2, 12), 55)


def add_hyperlinks(ws, url_col_idx):
    """Convert Asana Task URL cells to real Excel hyperlinks."""
    if not url_col_idx:
        return
    for row in ws.iter_rows(min_row=2, max_row=ws.max_row):
        cell = row[url_col_idx - 1]
        url = str(cell.value or '').strip()
        if url.startswith('http'):
            cell.hyperlink = url
            cell.font = Font(color='FF0563C1', underline='single')
            cell.value = 'Open in Asana'


def add_summary_sheet(wb, df):
    ws = wb.create_sheet('Summary')
    ws.column_dimensions['A'].width = 24
    ws.column_dimensions['B'].width = 22
    ws.column_dimensions['C'].width = 60

    # ---- ORDER_PICK counts ----
    op_col = next((c for c in df.columns if c == 'ORDER_PICK'), None)
    if op_col:
        for col, txt in enumerate(['ORDER_PICK', 'Count'], 1):
            c = ws.cell(row=1, column=col, value=txt)
            c.fill = HEADER_FILL
            c.font = HEADER_FONT
        counts = df[op_col].value_counts()
        for i, (val, cnt) in enumerate(counts.items(), 2):
            ws.cell(row=i, column=1, value=val)
            ws.cell(row=i, column=2, value=cnt)
            fill_hex = OP_FILL.get(val.upper() if val else '')
            if fill_hex:
                fill = PatternFill('solid', fgColor=fill_hex)
                ws.cell(row=i, column=1).fill = fill
                ws.cell(row=i, column=2).fill = fill
        next_row = len(counts) + 2
    else:
        next_row = 2

    total_row = next_row + 1
    ws.cell(row=total_row, column=1, value='Total rows').font = Font(bold=True)
    ws.cell(row=total_row, column=2, value=len(df)).font = Font(bold=True)

    # ---- Override instructions ----
    inst_row = total_row + 3
    title = ws.cell(row=inst_row, column=1,
                    value='HOW TO OVERRIDE  —  edit Column A on the BOM Review sheet')
    ws.merge_cells(start_row=inst_row, start_column=1, end_row=inst_row, end_column=3)
    title.fill = HEADER_FILL
    title.font = Font(bold=True, color='FFFFFFFF', size=12)
    title.alignment = Alignment(horizontal='center')

    hdr_row = inst_row + 1
    for col, txt in enumerate(['Override Value', 'Effect', 'When to use'], 1):
        c = ws.cell(row=hdr_row, column=col, value=txt)
        c.fill = PatternFill('solid', fgColor='FF2E75B6')
        c.font = Font(bold=True, color='FFFFFFFF')

    overrides = [
        ('FORCE_ORDER', 'Force to ORDER',
         'Part matched Golden Index as PICK but is bespoke / must be ordered. '
         'Use for any Substring match where the stock name looks unrelated to this part.'),
        ('SKIP', 'Exclude entirely',
         'Part should not appear in Asana at all (duplicate, error row, or not needed for this job).'),
        ('HOLD', 'Exclude (same as SKIP)',
         'Part deferred — not required yet. Treated identically to SKIP in Stage 5.'),
        ('{number}  e.g. 12', 'Override order quantity',
         'Force a specific reorder qty instead of BOM qty. Leave ORDER_PICK column unchanged.'),
        ('(leave blank)', 'No change',
         'Pipeline uses the ORDER_PICK result as-is. Default for most rows.'),
    ]
    override_fills = {'FORCE_ORDER': 'FFFFC7CE', 'SKIP': 'FFFFE699', 'HOLD': 'FFFFE699'}
    data_start = hdr_row + 1
    for i, (val, effect, when) in enumerate(overrides):
        r = data_start + i
        c1 = ws.cell(row=r, column=1, value=val)
        c2 = ws.cell(row=r, column=2, value=effect)
        c3 = ws.cell(row=r, column=3, value=when)
        c3.alignment = Alignment(wrap_text=True)
        ws.row_dimensions[r].height = 38
        key = val.split()[0].upper()
        hex_ = override_fills.get(key)
        if hex_:
            fill = PatternFill('solid', fgColor=hex_)
            c1.fill = fill
            c2.fill = fill

    # ---- Match Type warning ----
    warn_row = data_start + len(overrides) + 2
    warn_title = ws.cell(row=warn_row, column=1,
                         value='MATCH TYPE WARNING — verify Column K (Matched Stock_Number) on BOM Review')
    ws.merge_cells(start_row=warn_row, start_column=1, end_row=warn_row, end_column=3)
    warn_title.fill = PatternFill('solid', fgColor='FFC00000')
    warn_title.font = Font(bold=True, color='FFFFFFFF')
    warn_title.alignment = Alignment(horizontal='left')

    warn_hdr = warn_row + 1
    for col, txt in enumerate(['Match Type', 'Risk', 'Action'], 1):
        c = ws.cell(row=warn_hdr, column=col, value=txt)
        c.fill = PatternFill('solid', fgColor='FFC00000')
        c.font = Font(bold=True, color='FFFFFFFF')

    warn_rows = [
        ('Substring', 'HIGH — false positive likely',
         'Does Column I (Asana Task Name) make sense for this part? '
         'If unrelated → enter FORCE_ORDER in Column A immediately.'),
        ('Exact / Token', 'Low',
         'Part number matched precisely. Spot-check but usually safe to leave.'),
        ('No match → ORDER', 'None (expected)',
         'Bespoke part with no Golden Index entry. Routed to ORDER automatically. '
         'No action needed unless you want to add it to the index for future runs.'),
    ]
    warn_fills = {
        'Substring':       'FFFFC7CE',
        'Exact / Token':   'FFC6EFCE',
        'No match → ORDER': 'FFD9D9D9',
    }
    wdata_start = warn_hdr + 1
    for i, (mtype, risk, action) in enumerate(warn_rows):
        r = wdata_start + i
        c1 = ws.cell(row=r, column=1, value=mtype)
        c2 = ws.cell(row=r, column=2, value=risk)
        c2.font = Font(bold=True)
        c3 = ws.cell(row=r, column=3, value=action)
        c3.alignment = Alignment(wrap_text=True)
        ws.row_dimensions[r].height = 38
        hex_ = warn_fills.get(mtype)
        if hex_:
            fill = PatternFill('solid', fgColor=hex_)
            c1.fill = fill
            c2.fill = fill


def main():
    parser = argparse.ArgumentParser(description='Stage 4 -- Staging Export')
    parser.add_argument('--input', '-i', required=True)
    parser.add_argument('--output', '-o', default=None)
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input not found: {input_path}")
        sys.exit(1)

    date_str = datetime.now().strftime('%Y%m%d')
    output_path = Path(args.output) if args.output else \
        Path(f'BOM_Staging_Review_{date_str}.xlsx')

    print(f"Loading: {input_path}")
    df = pd.read_csv(input_path, dtype=str).fillna('')
    print(f"  {len(df)} rows")

    # Add standard columns if missing
    if 'Override' not in df.columns:
        df['Override'] = ''
    if 'ORDER_PICK' not in df.columns:
        df['ORDER_PICK'] = 'ORDER'
    if 'Order reference' not in df.columns:
        df['Order reference'] = ''
    if 'Order Status' not in df.columns:
        df['Order Status'] = df['ORDER_PICK'].map(
            lambda x: 'PICK' if str(x).upper() == 'PICK' else 'To Order'
        )

    # Apply column renames (description -> BOM Description etc.)
    df = df.rename(columns=COL_RENAMES)

    # Drop excluded columns (after rename so original names still match)
    exclude = {'Asana Task GID', 'Asana Task Description', 'Stock Check Status', 'Status',
               'assembly pack name', 'assembly pack details'}
    df = df.drop(columns=[c for c in exclude if c in df.columns], errors='ignore')

    # Build ordered column list: priority front, remainder after
    existing = {c.lower(): c for c in df.columns}
    front = []
    for pc in PRIORITY_COLS:
        match = existing.get(pc.lower())
        if match and match not in front:
            front.append(match)
    remainder = [c for c in df.columns if c not in front]
    df = df[front + remainder]

    print(f"Writing: {output_path}")
    df.to_excel(output_path, index=False, sheet_name='BOM Review', engine='openpyxl')

    wb = load_workbook(output_path)
    ws = wb.active
    header = [cell.value for cell in ws[1]]

    op_col      = col_idx(header, 'ORDER_PICK')
    url_col     = col_idx(header, 'Asana Task URL')
    ovr_col     = col_idx(header, 'Override')
    ls_col      = col_idx(header, 'Low Stock Alert')

    apply_styles(ws, op_col, ls_col)
    add_hyperlinks(ws, url_col)
    add_summary_sheet(wb, df)

    if ovr_col:
        ws.cell(row=1, column=ovr_col,
                value='Override / Reorder QTY\n(SKIP | HOLD | FORCE_ORDER | or enter a number to reorder that qty)')

    wb.save(output_path)

    order_cnt = (df['ORDER_PICK'] == 'ORDER').sum() if 'ORDER_PICK' in df.columns else '?'
    pick_cnt  = (df['ORDER_PICK'] == 'PICK').sum()  if 'ORDER_PICK' in df.columns else '?'

    print(f"\n{'='*55}")
    print(f"  Rows exported : {len(df)}")
    print(f"  ORDER         : {order_cnt}")
    print(f"  PICK          : {pick_cnt}")
    print(f"  Output        : {output_path}")
    print(f"{'='*55}")


if __name__ == '__main__':
    main()
