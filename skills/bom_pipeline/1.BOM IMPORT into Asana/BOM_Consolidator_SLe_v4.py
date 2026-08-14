
import pandas as pd
from pathlib import Path
import sys
import re
import yaml
from datetime import datetime
import shutil
import tempfile

# ── Load canonical source paths from PATH_CONFIG ────────────────────────────
_CONFIG_PATH = Path(__file__).parents[3] / "_copilot" / "PATH_CONFIG.yaml"
try:
    with open(_CONFIG_PATH) as _f:
        _CFG = yaml.safe_load(_f)
    _BOM_DUMP  = Path(_CFG["dropbox"]["bom_dump"])
    _BOM_MORPH = Path(_CFG["dropbox"]["bom_morph"])
except Exception:
    # Fallback if PATH_CONFIG unavailable (standalone runs outside SES_Workspace)
    _BOM_DUMP  = Path(r"C:\Users\GilesHugo\SAS Dropbox\4. External Operations\7.4 Purchasing Dept\Asana CSV Import Tools\Excel_BOM_Dump")
    _BOM_MORPH = Path(r"C:\Users\GilesHugo\SAS Dropbox\4. External Operations\7.4 Purchasing Dept\Asana CSV Import Tools\MORPH_BOM_Inputs")

# --- CONFIGURATION ---
# Where to save the final output file (local pipeline folder — updated from old Dropbox path)
OUTPUT_FILE = Path(__file__).parent / "Consolidated_BOMs" / "compiled_parts_list_with_assembly.csv"

# --- Column Names ---
# The name of the column containing the part number in BOTH file types.
# *** This is now lowercase and will be matched case-insensitively. ***
PART_NUMBER_COLUMN = 'part number'

# --- VERSION INFO ---
# v4: Updated to use "Drawing Pack" identifier method instead of tree pattern matching
# Drawing Pack identifier is found in Column L (Cost Centre) with pack name in Column D


# --- SCRIPT LOGIC (No need to edit below this line) ---

def build_lookup_map(legacy_folder: Path) -> dict:
    r"""
    Scans the legacy BOMs and creates a dictionary that maps each Part Number to its Drawing Pack name.

    NEW METHOD (v4):
    - Looks for rows where Column L (Cost Centre) = "Drawing Pack"
    - Column D (Part Number) in that row contains the pack name
    - All subsequent rows with non-empty Column L belong to that pack
    - Continues until next "Drawing Pack" identifier row

    Returns:
        dict: {'part_to_pack': {part_number: pack_name, ...}}
    """
    print("Step 1: Building the Drawing Pack lookup map from Legacy BOMs...")
    part_to_assembly_map = {}  # {part: last_pack} for single pack lookup
    part_to_all_assemblies = {}  # {part: [(pack1, qty1), (pack2, qty2), ...]} for all packs

    legacy_files = list(legacy_folder.glob("*.xlsx")) + list(legacy_folder.glob("*.xls"))
    if not legacy_files:
        print(f"Error: No Excel files found in the legacy BOM folder: {legacy_folder}")
        sys.exit()

    for file_path in legacy_files:
        try:
            # Try to read the file; if locked, copy to temp location and read the copy
            try:
                df_legacy = pd.read_excel(file_path)
            except PermissionError:
                print(f"    File locked, using temp copy: {file_path.name}")
                temp_dir = Path(tempfile.gettempdir())
                temp_copy = temp_dir / (file_path.name + ".tmp.xlsx")
                try:
                    shutil.copyfile(str(file_path), str(temp_copy))
                    df_legacy = pd.read_excel(temp_copy)
                finally:
                    try:
                        if temp_copy.exists():
                            temp_copy.unlink()
                    except Exception:
                        pass

            # Normalize all column headers to lowercase
            df_legacy.columns = [str(col).lower() for col in df_legacy.columns]

            # Find the required columns (case-insensitive)
            part_col = None
            cost_centre_col = None
            qty_col = None

            # Find Part Number column (Column D or by name)
            if PART_NUMBER_COLUMN in df_legacy.columns:
                part_col = PART_NUMBER_COLUMN
            elif len(df_legacy.columns) > 3:
                part_col = df_legacy.columns[3]  # Column D (index 3)

            # Find Cost Centre column (Column L or by name)
            for col in df_legacy.columns:
                if col.lower() in ['cost centre', 'cost center', 'cc']:
                    cost_centre_col = col
                    break

            # Find Item QTY column
            for col in df_legacy.columns:
                if col.lower() in ['item qty', 'qty', 'quantity']:
                    qty_col = col
                    break

            if not part_col or not cost_centre_col:
                print(f"  - Warning: Required columns not found in {file_path.name}. Skipping this file.")
                print(f"    Part column: {part_col}, Cost Centre column: {cost_centre_col}")
                continue

            # Track the current pack
            current_pack_name = None
            parts_found = 0
            packs_found = 0

            for idx, row in df_legacy.iterrows():
                # Get values and normalize
                part_val = row[part_col]
                cost_centre_val = row[cost_centre_col]
                qty_val = row[qty_col] if qty_col else None

                part_str = str(part_val).strip() if pd.notna(part_val) else ""
                cost_centre_str = str(cost_centre_val).strip().upper() if pd.notna(cost_centre_val) else ""
                qty_str = str(qty_val).strip() if pd.notna(qty_val) else ""

                # Check if this row is a "Drawing Pack" identifier
                if cost_centre_str == "DRAWING PACK":
                    if not part_str:
                        print(f"    WARNING: Drawing Pack header with no name at row {idx} in {file_path.name}")
                        current_pack_name = "UNKNOWN_PACK"
                    else:
                        current_pack_name = part_str
                        packs_found += 1
                        print(f"    Found Drawing Pack: '{current_pack_name}'")
                    continue  # Skip this row, it's a header not a part

                # Assign parts to current pack
                if cost_centre_str and cost_centre_str != "":  # Non-empty cost centre
                    if current_pack_name is None:
                        print(f"    WARNING: Part '{part_str}' at row {idx} has no pack (appears before first Drawing Pack header)")
                        current_pack_name = "UNASSIGNED"

                    if part_str:
                        # Track last pack for backward compatibility
                        part_to_assembly_map[part_str] = current_pack_name

                        # Track all packs with quantities
                        if part_str not in part_to_all_assemblies:
                            part_to_all_assemblies[part_str] = []
                        part_to_all_assemblies[part_str].append({
                            'pack': current_pack_name,
                            'qty': qty_str
                        })

                        parts_found += 1

            print(f"  - Processed {file_path.name} -> found {packs_found} packs, assigned {parts_found} parts")

        except Exception as e:
            print(f"  - Error processing {file_path.name}: {e}")

    if not part_to_assembly_map:
        print("WARNING: No parts assigned to packs. Check if legacy BOMs use 'Drawing Pack' identifiers in Column L.")

    print(f"--> Lookup map created: {len(part_to_assembly_map)} parts mapped to packs.\n")
    print(f"    Parts with multiple packs: {sum(1 for p in part_to_all_assemblies.values() if len(p) > 1)}\n")
    return {
        'part_to_pack': part_to_assembly_map,
        'part_to_all_packs': part_to_all_assemblies
    }

def process_main_boms(parts_folder: Path, lookup_map: dict) -> pd.DataFrame:
    """
    Reads all main Parts BOMs, combines them, and adds the new columns.
    """
    print("Step 2: Processing and combining all main Parts BOMs...")
    all_boms_data = []

    def _find_column_any(df: pd.DataFrame, targets):
        targets_lower = [t.lower() for t in (targets if isinstance(targets, (list, tuple)) else [targets])]
        for col in df.columns:
            cl = str(col).lower()
            if cl in targets_lower:
                return col
        return None

    # Recursively find Excel files in all subfolders of the parts folder
    part_files = list(parts_folder.rglob("*.xlsx")) + list(parts_folder.rglob("*.xls"))
    if not part_files:
        print(f"Error: No Excel files found in the parts BOM folder (including subfolders): {parts_folder}")
        sys.exit()

    for file_path in part_files:
        try:
            df_part = pd.read_excel(file_path)

            # Normalize all column headers to lowercase
            df_part.columns = [str(col).lower() for col in df_part.columns]

            # Add a new column with the name of the source file
            df_part['source bom file'] = file_path.name
            all_boms_data.append(df_part)
            print(f"  - Loaded {file_path.name}")

        except Exception as e:
            print(f"  - Error processing {file_path.name}: {e}")

    # Combine all individual BOM dataframes into one large one
    if not all_boms_data:
        print("Error: No data was loaded from the parts BOM files. Exiting.")
        sys.exit()

    combined_df = pd.concat(all_boms_data, ignore_index=True)
    print(f"--> Combined {len(all_boms_data)} BOMs into a single list of {len(combined_df)} parts.\n")

    print("Step 3: Looking up Drawing Pack names for each part...")

    # Check if the normalized PART_NUMBER_COLUMN exists before mapping
    if PART_NUMBER_COLUMN not in combined_df.columns:
        print(f"Error: The key column '{PART_NUMBER_COLUMN}' was not found in the combined BOMs.")
        print("Please check the files in:", parts_folder)
        sys.exit()

    # Extract part-to-pack mapping
    part_to_pack = lookup_map.get('part_to_pack', {})
    part_to_all_packs = lookup_map.get('part_to_all_packs', {})

    def resolve_pack(row: pd.Series) -> str:
        """Resolve drawing pack by direct part number lookup."""
        pn = str(row.get(PART_NUMBER_COLUMN, '')).strip()

        if not pn:
            return None

        # Direct lookup in part_to_pack map
        return part_to_pack.get(pn, None)

    def resolve_all_packs(row: pd.Series) -> str:
        """Resolve all drawing packs with quantities for parts appearing in multiple packs."""
        pn = str(row.get(PART_NUMBER_COLUMN, '')).strip()

        if not pn:
            return None

        # Get all packs for this part
        all_packs = part_to_all_packs.get(pn, [])

        if len(all_packs) <= 1:
            return None  # Single pack or no pack, don't need details

        # Format: "qty1|pack1;qty2|pack2;qty3|pack3"
        pack_strings = [f"{p['qty']}|{p['pack']}" for p in all_packs]
        return ";".join(pack_strings)

    combined_df['assembly pack name'] = combined_df.apply(resolve_pack, axis=1)
    combined_df['assembly pack details'] = combined_df.apply(resolve_all_packs, axis=1)

    print(f"Initial rows before filtering: {len(combined_df)}")

    # If Authority is present, conditionally force Cost Centre and Vendor values
    # ONLY for Finishes and Marine BOMs
    cost_centre_col = _find_column_any(combined_df, ['cost centre', 'cost center'])
    authority_col = _find_column_any(combined_df, 'authority')
    vendor_col = _find_column_any(combined_df, 'vendor')

    if authority_col:
        # Identify Finishes and Marine BOM files
        finishes_mask = combined_df['source bom file'].str.contains('Finishes', case=False, na=False)
        marine_mask = combined_df['source bom file'].str.contains('Marine', case=False, na=False)
        finishes_or_marine = finishes_mask | marine_mask

        # Only parts with authority value
        authority_mask = combined_df[authority_col].notna() & (combined_df[authority_col].astype(str).str.strip() != '')

        # Combine conditions: has authority AND is from Finishes/Marine BOM
        finishes_authority_mask = authority_mask & finishes_or_marine

        # Apply overwrites only to qualified rows
        if cost_centre_col:
            combined_df.loc[finishes_authority_mask, cost_centre_col] = "Finishes BOM"
        if vendor_col:
            combined_df.loc[finishes_authority_mask, vendor_col] = combined_df.loc[finishes_authority_mask, authority_col]

    # Filter to ONLY include rows with a value in the COST CENTRE column
    if cost_centre_col:
        combined_df = combined_df[combined_df[cost_centre_col].notna() & (combined_df[cost_centre_col].astype(str).str.strip() != '')]
        print(f"After filtering for COST CENTRE values: {len(combined_df)} rows retained\n")
    else:
        print("Warning: 'cost centre' column not found. Proceeding without filtering.\n")

    # Reorder columns to put the new ones at the front
    original_cols = [col for col in combined_df.columns if col not in ['assembly pack name', 'assembly pack details', 'source bom file']]
    final_cols = ['assembly pack name', 'assembly pack details', 'source bom file'] + original_cols
    final_df = combined_df[final_cols]

    print("--> Lookup complete.\n")
    return final_df

def run_logic(parts_folder: Path, legacy_folder=None) -> Path:
    """
    Run the consolidator flow. Returns output CSV path.

    Args:
        parts_folder: Folder containing the main Excel BOM files (required).
        legacy_folder: Folder containing legacy assembly-pack BOMs (optional).
                       If omitted, assembly pack attribution is skipped and
                       'assembly pack name' column will be empty — ready for
                       the later drawing-pack attribution workflow.
    """
    if not parts_folder.exists():
        print(f"ERROR: Parts BOM folder does not exist: {parts_folder}")
        sys.exit(1)

    if legacy_folder is not None:
        if not legacy_folder.exists():
            print(f"ERROR: Legacy BOM folder does not exist: {legacy_folder}")
            sys.exit(1)
        lookup_index = build_lookup_map(legacy_folder)
        if not lookup_index:
            print("Could not build the lookup map. Check legacy BOM files.")
            sys.exit(1)
    else:
        print("Step 1: No legacy BOM folder provided — skipping assembly pack lookup.")
        print("        'assembly pack name' will be empty (populated later via drawing-pack workflow).\n")
        lookup_index = {'part_to_pack': {}, 'part_to_all_packs': {}}

    final_dataframe = process_main_boms(parts_folder, lookup_index)

    # Save the final result to a CSV file
    try:
        final_dataframe.to_csv(OUTPUT_FILE, index=False)
        print(f"Success: The final compiled file has been saved to:\n{OUTPUT_FILE}")
        return OUTPUT_FILE
    except Exception as e:
        print(f"Error: Could not save the final file. Please check the path and permissions. Error: {e}")
        # Fallback to a timestamped file
        alt_path = OUTPUT_FILE.with_name(f"{OUTPUT_FILE.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        try:
            final_dataframe.to_csv(alt_path, index=False)
            print(f"Saved to fallback file:\n{alt_path}")
            return alt_path
        except Exception as e2:
            print(f"Fallback save failed: {e2}")
            sys.exit(1)

def main():
    """CLI entry point to maintain backwards compatibility."""
    if len(sys.argv) >= 3:
        parts_folder = Path(sys.argv[1])
        legacy_folder = Path(sys.argv[2])
    else:
        parts_folder = _BOM_DUMP
        legacy_folder = _BOM_MORPH

    run_logic(parts_folder, legacy_folder)


if __name__ == "__main__":
    main()
