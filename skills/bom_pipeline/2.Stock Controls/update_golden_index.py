"""
Golden Index Updater
====================
Reads a reviewed staging Excel (Stage 4 output) and upserts any rows that have
a Mapped KAN value into the Golden Index CSV.

For each row with a non-empty Mapped KAN:
  - Strips the project prefix (e.g. '1566.') from the Part Number to get CAD PN
  - If CAD PN already exists in the index: updates Mapped KAN (and flags as 'Manual Revised')
  - If CAD PN is new: appends a fresh row

Part prefix stripping rule:
  '^NNNN.' (4-digit project number + period) is removed from the start.
  '1566.1003.B.087' -> '1003.B.087'
  '1566.1008.T.13ID_P_Clip' -> '1008.T.13ID_P_Clip'

Usage:
    python update_golden_index.py \
        --staging BOM_Staging_Review_20260313.xlsx \
        --golden-index "2.Stock Controls/Merged_Golden_Index_Rev2.csv"

    # Save to a new dated file instead of overwriting:
    python update_golden_index.py \
        --staging BOM_Staging_Review_20260313.xlsx \
        --golden-index Merged_Golden_Index_Rev2.csv \
        --output Merged_Golden_Index_20260313.csv
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd


def strip_project_prefix(part_number: str) -> str:
    """
    Remove the leading project-ID prefix ('NNNN.') from a Sesame part number.
    '1566.1003.B.087'          -> '1003.B.087'
    '1234.1000.A.001'          -> '1000.A.001'
    '005-099-001_Shrinkwrap'   -> '005-099-001_Shrinkwrap'  (no prefix — unchanged)
    """
    return re.sub(r'^\d{4}\.', '', part_number.strip())


def load_golden(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str).fillna('')
    df.columns = [c.strip() for c in df.columns]
    return df


def main():
    parser = argparse.ArgumentParser(description='Update Golden Index from staging sheet')
    parser.add_argument('--staging', '-s', required=True,
                        help='Stage 4 staging Excel (BOM_Staging_Review_*.xlsx)')
    parser.add_argument('--sheet', default='BOM Review',
                        help='Sheet name in staging Excel (default: BOM Review)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Print changes without writing')
    args = parser.parse_args()

    staging_path = Path(args.staging)
    
    # Hardcoded Golden Index paths (Rev4)
    gi_bot_path = Path(r"C:\Users\GilesHugo\SES_Workspace\skills\bom_pipeline\2.Stock Controls\Merged_Golden_Index_Rev4.csv")
    gi_lab_path = Path(r"C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\Asana Scripts\2.Stock Controls\Merged_Golden_Index_Rev4.csv")

    if not staging_path.exists():
        print(f"ERROR: staging file not found: {staging_path}")
        sys.exit(1)
    if not gi_bot_path.exists():
        print(f"ERROR: Bot golden index copy not found: {gi_bot_path}")
        sys.exit(1)

    # ------------------------------------------------------------------ load
    print(f"Loading staging: {staging_path}")
    staging = pd.read_excel(staging_path, sheet_name=args.sheet, dtype=str).fillna('')
    print(f"  {len(staging)} rows")

    print(f"Loading golden index (Bot Execution Copy): {gi_bot_path}")
    golden = load_golden(gi_bot_path)
    print(f"  {len(golden)} existing entries")

    # Case-insensitive column lookup
    def find(df, name):
        for c in df.columns:
            if c.lower() == name.lower():
                return c
        return None

    part_col        = find(staging, 'Part Number')
    kan_col         = find(staging, 'Mapped KAN')
    name_col        = find(staging, 'Asana Task Name')
    url_col         = find(staging, 'Asana Task URL')
    desc_col        = find(staging, 'BOM Description')
    matched_sn_col  = find(staging, 'Matched Stock_Number')   # Rev4 key — prefer over stripped PN

    if not part_col or not kan_col:
        print("ERROR: staging sheet must have 'Part Number' and 'Mapped KAN' columns")
        sys.exit(1)

    # Build a lookup: Stock_Number (lower) -> row index in golden (Rev4 col C)
    golden_lookup: dict[str, int] = {
        str(r['Stock_Number']).strip().lower(): i
        for i, r in golden.iterrows()
        if r.get('Stock_Number', '').strip()
    }

    updated = 0
    appended = 0
    new_rows = []

    for _, row in staging.iterrows():
        raw_pn = str(row.get(part_col, '')).strip()
        kan    = str(row.get(kan_col, '')).strip()

        if not raw_pn or not kan:
            continue  # skip rows with no part number or no KAN

        # Use Matched Stock_Number (from Stage 2 match) if present — it IS the Rev4 key.
        # Fall back to stripped PN only for manually entered KANs with no prior match.
        matched_sn = str(row.get(matched_sn_col, '')).strip() if matched_sn_col else ''
        cad_pn = matched_sn if matched_sn else strip_project_prefix(raw_pn)
        key = cad_pn.lower()

        asana_name = str(row.get(name_col, '')).strip() if name_col else ''
        asana_url  = str(row.get(url_col, '')).strip()  if url_col  else ''
        bom_desc   = str(row.get(desc_col, '')).strip()  if desc_col  else ''

        if key in golden_lookup:
            # Update existing entry (Rev4 column names)
            idx = golden_lookup[key]
            old_kan = golden.at[idx, 'Mapped_KAN']
            if old_kan != kan:
                print(f"  UPDATE  '{cad_pn}': KAN {old_kan!r} -> {kan!r}")
                golden.at[idx, 'Mapped_KAN']  = kan
                golden.at[idx, 'Match_Type']  = 'Manual Revised'
                golden.at[idx, 'Confidence']  = '100'
                updated += 1
            # else: same value, no change
        else:
            # New entry — populate Rev4 columns
            print(f"  APPEND  '{cad_pn}' -> {kan}")
            new_rows.append({
                'Source':               'Manual',
                'Source_Files':         str(staging_path.name),
                'Stock_Number':         cad_pn,
                'CAD_PNs':              cad_pn,
                'Part_Numbers':         raw_pn,
                'Processed_PNs':        cad_pn,
                'Descriptions':         bom_desc,
                'Total_QTY':            '1',
                'Mapped_KAN':           kan,
                'Asana_Name':           asana_name,
                'Asana_Vendors_PN':     '',
                'Asana_TYPE_OF_STOCK':  '',
                'Asana_URL':            asana_url,
                'Confidence':           '100',
                'Match_Type':           'Manual',
                'Provisional':          'NO',
            })
            golden_lookup[key] = len(golden) + len(new_rows) - 1
            appended += 1

    if new_rows:
        golden = pd.concat([golden, pd.DataFrame(new_rows)], ignore_index=True)

    print(f"\nSummary: {updated} updated, {appended} appended")

    if args.dry_run:
        print("(dry-run — no file written)")
        return

    if updated == 0 and appended == 0:
        print("No changes - golden index unchanged.")
        return

    # Write to both bot copy and lab copy
    try:
        golden.to_csv(gi_bot_path, index=False)
        print(f"Golden index saved to Bot Copy: {gi_bot_path}")
    except Exception as e:
        print(f"ERROR writing to Bot Copy: {e}")

    try:
        golden.to_csv(gi_lab_path, index=False)
        print(f"Golden index saved to Lab Copy: {gi_lab_path}")
    except Exception as e:
        print(f"ERROR writing to Lab Copy: {e}")


if __name__ == '__main__':
    main()
