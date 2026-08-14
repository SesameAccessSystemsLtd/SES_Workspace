"""
Stage 5 — BOM Import to Asana (v4)
=====================================
Reads the reviewed staging Excel (from Stage 4), creates a hierarchical Asana
task structure directly via API, and outputs a task manifest CSV for use in
Stage 6 and 7 (drawing pack references, PO updates).

Key differences from v3:
  - Input: BOM_Staging_Review_*.xlsx (reviewed staging sheet, not raw compiled CSV)
  - Direct Asana API task creation (no manual CSV upload step)
  - Respects Override column: SKIP / HOLD → excluded; FORCE_ORDER → included regardless
  - Sets 'Sesame Part No' custom field on each part task at creation
  - Task name uses spaced part number convention: "1234. 1004.a.003"
  - Outputs task_manifest_{date}.csv with Task GID and URL for downstream stages

Target project: 848657415032331 (override with --project-gid)

Usage (DRY-RUN — no Asana writes):
    python "BOM IMPORT into Asana/BOM_IMPORT_4_Asana_v4.py" \
        --input BOM_Staging_Review_20260310.xlsx \
        --project-name "1501 - Downing Street" \
        --dropbox-url "https://..." \
        --asana-url "https://..."

Commit (creates tasks in Asana):
    ... --commit
"""

import argparse
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

# Resolve asana_updater relative to parent (2.Stock Controls)
sys.path.insert(0, str(Path(__file__).parent.parent / '2.Stock Controls'))
from asana_updater import AsanaUpdater

# Resolve db_writer in purchasing skill
sys.path.insert(0, str(Path(__file__).parent.parent.parent / 'purchasing'))
from db_writer import write_bom_requirement

# Asana target project GID — HARDCODED to Build Orders project
DEFAULT_PROJECT_GID = '848657415032331'

# Custom field GID for part number lookup
SESAME_PART_NO_GID = '1213228090549470'

# Assignees
PURCHASING_EMAIL = 'purchasing@sesameaccess.com'
DEFAULT_EMAIL = 'Giles@sesameaccess.com'

ASANA_BASE_URL = 'https://app.asana.com/0'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def clean(value) -> str:
    if pd.isna(value) or value is None:
        return ''
    return str(value).strip()


def find_col(df: pd.DataFrame, *names: str) -> str | None:
    """
    Case-insensitive column lookup. Tries each name in order, returns first match.
    Also handles headers that start with the candidate name followed by a newline
    (e.g. 'Override / Reorder QTY\n(SKIP | ...)' matched by 'Override / Reorder QTY').
    """
    # Normalise: take only the part before any newline, strip whitespace
    def norm(s: str) -> str:
        return s.split('\n')[0].strip().lower()

    normed_cols = {norm(c): c for c in df.columns}
    for name in names:
        match = normed_cols.get(norm(name))
        if match:
            return match
    return None


def space_part_number(part: str) -> str:
    """
    Insert a space after the first numeric segment of a part number.
    '1234.1004.a.003'  →  '1234. 1004.a.003'
    '1535.1000.B.30x17' → '1535. 1000.B.30x17'
    """
    return re.sub(r'^(\d+)\.', r'\1. ', part)


def determine_assignee(name: str) -> str:
    if 'sesame' in name.lower() or 'nbss' in name.lower():
        return PURCHASING_EMAIL
    return DEFAULT_EMAIL


def task_url(project_gid: str, task_gid: str) -> str:
    return f"{ASANA_BASE_URL}/{project_gid}/{task_gid}"


# ---------------------------------------------------------------------------
# Row filtering & ORDER/PICK routing
# ---------------------------------------------------------------------------

def _is_numeric(val: str) -> bool:
    try:
        float(val); return True
    except (ValueError, TypeError):
        return False


def should_import(row, override_col) -> bool:
    """Return True if this row should be imported."""
    override = clean(row.get(override_col, ''))
    return override.upper() not in ('SKIP', 'HOLD')


def get_section(row, override_col, op_col) -> str:
    """Return 'ORDER' or 'PICK'. Numeric override or FORCE_ORDER always → ORDER."""
    override = clean(row.get(override_col, ''))
    if override.upper() == 'FORCE_ORDER' or _is_numeric(override):
        return 'ORDER'
    op = clean(row.get(op_col, '')).upper() if op_col else ''
    return 'PICK' if op == 'PICK' else 'ORDER'


def get_qty(row, qty_col, override_col) -> str:
    """Numeric Override takes precedence over BOM qty as the order quantity."""
    override = clean(row.get(override_col, ''))
    if _is_numeric(override):
        return str(int(float(override)))
    return clean(row.get(qty_col, '')) if qty_col else ''


# ---------------------------------------------------------------------------
# Hierarchy builder
# ---------------------------------------------------------------------------

class HierarchyManager:
    """Tracks GIDs of already-created parent/group tasks to avoid duplicates."""
    def __init__(self, updater: AsanaUpdater, project_gid: str, commit: bool, pause: float):
        self.updater = updater
        self.project_gid = project_gid
        self.commit = commit
        self.pause = pause
        self._gid_cache: dict[str, str] = {}  # name → gid

    def get_or_create(self, name: str, parent_name: str | None, notes: str = '',
                      add_to_project: bool = False, custom_fields: dict | None = None) -> str:
        """Return cached GID or create a new task/subtask. In dry-run returns placeholder.
        If add_to_project=True, also adds the task to the project so it appears in project
        views and project-scoped custom fields become accessible.
        If custom_fields provided, sets them after task creation.
        """
        if name in self._gid_cache:
            return self._gid_cache[name]

        if not self.commit:
            fake_gid = f'DRY_{len(self._gid_cache):04d}'
            self._gid_cache[name] = fake_gid
            print(f"    [DRY] Would create: '{name}'")
            return fake_gid

        parent_gid = self._gid_cache.get(parent_name) if parent_name else None

        if parent_gid and not parent_gid.startswith('DRY'):
            data = self.updater.create_subtask(parent_gid, name, notes=notes,
                                               assignee=determine_assignee(name))
        else:
            data = self.updater.create_task(self.project_gid, name, notes=notes,
                                            assignee=determine_assignee(name))

        gid = data.get('gid', '')
        self._gid_cache[name] = gid

        if add_to_project and gid:
            try:
                self.updater.add_to_project(gid, self.project_gid)
                # Set custom fields after adding to project
                if custom_fields:
                    try:
                        self.updater.update_task(gid, custom_fields=custom_fields)
                    except Exception as e:
                        print(f"    [WARN] Could not set custom fields on '{name}': {e}")
            except Exception as e:
                print(f"    [WARN] Could not add '{name}' to project: {e}")

        print(f"    [CREATED] '{name}' -> GID {gid}")
        time.sleep(self.pause)
        return gid


# ---------------------------------------------------------------------------
# Asana CSV row builder
# ---------------------------------------------------------------------------

def _csv_row(name: str, notes: str = '', subtask_of: str = '',
             order_qty: str = '', vendor: str = '', assignee: str = '',
             order_pick: str = '', stock_action: str = '') -> dict:
    return {
        'Name':        name,
        'Description': notes,
        'Subtask of':  subtask_of,
        'ORDER QTY':   order_qty,
        'Vendor':      vendor,
        'Assignee':    assignee,
        'Email':       '',
        'Due Date':    '',
        'ORDER_PICK':  order_pick,
        'Stock Action': stock_action,
        'Asana Task GID': '',
        'Asana Task URL': '',
    }


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process(df: pd.DataFrame, project_name: str, dropbox_url: str, asana_url: str,
            project_gid: str, updater: AsanaUpdater, commit: bool, pause: float,
            kan_urls: dict | None = None, golden_index_path: str = '') -> list[dict]:
    """Build hierarchy, create tasks in Asana (if commit), return Asana-CSV manifest."""
    date_str = datetime.now().strftime('%Y%m%d')

    # Find required columns — each entry is (key, *candidate_names).
    # Stage 4 renames some columns; supply both original and renamed forms.
    col_aliases = [
        ('part number',    'Part Number',    'part number'),
        ('item qty',       'BOM QTY',        'item qty'),
        ('description',    'BOM Description','description'),
        ('override',       'Override / Reorder QTY', 'Override', 'override'),
        ('order_pick',     'ORDER_PICK',     'order_pick'),
        ('mapped kan',     'Mapped KAN',     'mapped kan'),
        ('stock number',   'stock number'),
        ('cost center',    'cost center'),
        ('notes',          'notes'),
        ('web link',       'web link'),
        ('thickness',      'thickness'),
        ('material',       'material'),
        ('mass',           'mass'),
        ('volume',         'volume'),
        ('authority',      'authority'),
        ('vendor',         'Supplier', 'Vendor', 'vendor'),
        ('source bom file','source bom file'),
    ]
    col_map = {}
    for entry in col_aliases:
        key, *candidates = entry
        col_map[key] = find_col(df, *candidates)

    override_col = col_map.get('override') or 'Override / Reorder QTY'
    op_col       = col_map.get('order_pick')

    rows_to_import = [r for _, r in df.iterrows() if should_import(r, override_col)]
    print(f"\nRows to import: {len(rows_to_import)} of {len(df)} total")

    if not rows_to_import:
        print("Nothing to import — all rows excluded by Override or Classification.")
        return []

    # ------------------------------------------------------------------
    # Load Golden Index to detect manual KAN amendments
    # ------------------------------------------------------------------
    gi_dict = {}
    if golden_index_path:
        gi_path = Path(__file__).parent / golden_index_path
        if gi_path.exists():
            gi_df = pd.read_csv(gi_path, dtype=str).fillna('')
            for _, r in gi_df.iterrows():
                cad = str(r.get('Stock_Number', '')).strip().lower()  # Rev4 col C
                kan = str(r.get('Mapped_KAN', '')).strip()            # Rev4 col I
                if cad:
                    gi_dict[cad] = kan
        else:
            print(f"WARNING: Golden Index not found at {gi_path.resolve()} — unable to check manual KAN amendments.")

    # ------------------------------------------------------------------
    # CSV output tracker (ALL rows: hierarchy + part tasks)
    # Structure mirrors the Asana CSV import format.
    #   PICK items: grouped under vendor tasks (same as ORDER items)
    #   ORDER items: vendor groups directly under ALL BOMS
    #     Finishes/Marine: category BOM -> authority group -> parts
    # ------------------------------------------------------------------
    csv_rows: list[dict] = []
    csv_seen: set[str] = set()   # deduplicate hierarchy rows

    def csv_section(name: str, notes: str = '', subtask_of: str = '', vendor: str = '') -> None:
        if name not in csv_seen:
            csv_seen.add(name)
            csv_rows.append(_csv_row(name, notes=notes, subtask_of=subtask_of, vendor=vendor))

    # Top-level task
    top_name = f"ALL BOMS - {project_name}"
    top_notes = (f"Master task for all BOMs related to {project_name}\n"
                 f"Dropbox: {dropbox_url}\nProject: {asana_url}")
    csv_section(top_name, notes=top_notes)

    # All parts are grouped under vendor tasks; no separate PICKING BOM exists.

    hierarchy = HierarchyManager(updater, project_gid, commit, pause)
    hierarchy.get_or_create(top_name, None, notes=top_notes)

    part_col = col_map.get('part number')
    qty_col  = col_map.get('item qty')

    # Multi-pack deduplication: same part in >1 assembly pack → combined qty with *
    part_to_packs: dict[str, dict[str, float]] = {}
    for row in rows_to_import:
        pn = clean(row.get(part_col, '')) if part_col else ''
        try:
            qty = float(clean(row.get(qty_col, '0') or '0')) if qty_col else 0.0
        except ValueError:
            qty = 0.0
        if pn:
            part_to_packs.setdefault(pn, {})
            part_to_packs[pn][''] = part_to_packs[pn].get('', 0.0) + qty

    multi_pack_parts = {pn for pn, packs in part_to_packs.items() if len(packs) > 1}
    processed_multi: set[str] = set()

    for row in rows_to_import:
        pn          = clean(row.get(part_col, '')) if part_col else ''
        desc        = clean(row.get(col_map.get('description'), '')) if col_map.get('description') else ''
        qty_str     = get_qty(row, qty_col, override_col)
        stock_number = clean(row.get(col_map.get('stock number'), '')) if col_map.get('stock number') else ''
        vendor      = clean(row.get(col_map.get('vendor'), ''))      if col_map.get('vendor')       else ''
        authority   = clean(row.get(col_map.get('authority'), ''))   if col_map.get('authority')    else ''
        cost_center = clean(row.get(col_map.get('cost center'), '')) if col_map.get('cost center')  else ''
        notes       = clean(row.get(col_map.get('notes'), ''))       if col_map.get('notes')        else ''
        web_link    = clean(row.get(col_map.get('web link'), ''))    if col_map.get('web link')     else ''
        material    = clean(row.get(col_map.get('material'), ''))    if col_map.get('material')     else ''
        thickness   = clean(row.get(col_map.get('thickness'), ''))   if col_map.get('thickness')    else ''
        mass        = clean(row.get(col_map.get('mass'), ''))        if col_map.get('mass')         else ''
        volume      = clean(row.get(col_map.get('volume'), ''))      if col_map.get('volume')       else ''
        source      = clean(row.get(col_map.get('source bom file'), '')) if col_map.get('source bom file') else ''

        # Route by cost_center (set correctly by Stage 1 for both Finishes and Marine Lacquer).
        # Marine Lacquer parts WITH authority already have cost_center='Finishes BOM' after Stage 1.
        # Marine Lacquer parts WITHOUT authority (e.g. lasercut sub-parts) keep cost_center='Laser Cut'
        # and correctly fall through to the normal cost_center+vendor routing below.
        is_finishes = cost_center.lower() == 'finishes bom'
        effective_vendor = vendor

        # Multi-pack deduplication
        if pn in multi_pack_parts and pn in processed_multi:
            continue
        if pn in multi_pack_parts:
            processed_multi.add(pn)
            total_qty = sum(part_to_packs[pn].values())
            qty_text  = f"{int(total_qty)} off*"
            bom_qty   = int(total_qty)
        else:
            qty_text = f"{qty_str} off" if qty_str else ''
            bom_qty  = int(float(qty_str)) if qty_str and _is_numeric(qty_str) else 0

        # Task name
        spaced_pn  = space_part_number(pn) if pn else ''
        # Surface finish tasks: finish type before part number so they read as
        # "12 off Yellow Zinc Plating 1575. 1010.B.064 - Bottom Packer for Bearing"
        # and are visually distinct from the lasercut task for the same part number.
        if is_finishes:
            name_parts = [qty_text, effective_vendor, spaced_pn, f'- {desc}' if desc else '']
        else:
            name_parts = [qty_text, spaced_pn, f'- {desc}' if desc else '', stock_number, effective_vendor]
        task_name  = ' '.join(p for p in name_parts if p)

        # Task description / notes
        kan_val    = clean(row.get(col_map.get('mapped kan'), '')) if col_map.get('mapped kan') else ''
        kan_url    = (kan_urls or {}).get(row.name, '')
        if not kan_val:
            kan_url = ''
        
        desc_lines = [pn]
        if stock_number: desc_lines.append(f"Stock Number: {stock_number}")
        if kan_val:
            desc_lines.append(f"KAN: {kan_val}")
            if kan_url:
                desc_lines.append(f"KAN Stock Link: {kan_url}")
        if material:     desc_lines.append(f"Material: {material}")
        if thickness:    desc_lines.append(f"Thickness: {thickness}")
        if web_link:     desc_lines.append(f"Web Link: {web_link}")
        if cost_center:  desc_lines.append(f"From Cost Centre {cost_center}")
        desc_lines.append(f"Link to BOM: {dropbox_url}")
        if mass:         desc_lines.append(f"Mass: {mass}")
        if volume:       desc_lines.append(f"Volume: {volume}")
        desc_lines.append(f"Project Task: {asana_url}")
        if notes:        desc_lines.extend(['---', f"NOTES: {notes}"])
        task_notes = '\n'.join(l for l in desc_lines if l)

        # ---------------------------------------------------------------
        # Determine parent (CSV subtask_of + API parent GID)
        # ---------------------------------------------------------------
        section = get_section(row, override_col, op_col)
        
        # Check if KAN was manually amended
        cad_pn = re.sub(r'^\d{4}\.', '', pn.strip())
        gi_kan = gi_dict.get(cad_pn.lower(), '')
        kan_amended = bool(kan_val) and (kan_val != gi_kan)

        stock_action = 'PROJECT ORDER'
        if section == 'PICK':
            if not kan_amended:
                stock_action = 'PLEASE PICK'
            else:
                # KAN manually amended in staging -> Re-query Asana STOCK QTY
                if commit:
                    print(f"    [INFO] KAN amended to {kan_val} -> Re-querying stock...")
                    try:
                        # Find the KAN task in KANBAN project (632603478911321)
                        params = {
                            'custom_fields.1206590288351824.value': kan_val,
                            'projects.any': '632603478911321',
                            'opt_fields': 'custom_fields',
                            'limit': 1,
                        }
                        ws_resp = updater._request_with_retry('GET', f"{updater.base}/users/me")
                        workspace_gid = ws_resp.json()['data']['workspaces'][0]['gid']
                        
                        r = updater._request_with_retry('GET', f"{updater.base}/workspaces/{workspace_gid}/tasks/search", params=params)
                        kan_tasks = r.json().get('data', [])
                        
                        if kan_tasks:
                            k_task = kan_tasks[0]
                            stk_qty_val = AsanaUpdater.extract_custom_field_value(k_task, '1126782469949969')
                            stk_qty = float(stk_qty_val) if stk_qty_val is not None else 0.0
                            bom_req = float(qty_str) if qty_str and _is_numeric(qty_str) else 0.0
                            stock_action = 'PLEASE PICK' if stk_qty >= bom_req else 'PROJECT ORDER'
                        else:
                            stock_action = 'PROJECT ORDER'
                    except Exception as e:
                        print(f"    [WARN] Failed to query stock for {kan_val}: {e}")
                        stock_action = 'PLEASE PICK (dry-run assumes enough stock)'
                else:
                    stock_action = 'PLEASE PICK (dry-run assumes enough stock)'

        # Both PICK and ORDER items follow the same vendor grouping logic
        # PICK items get "PLEASE PICK" stock action, ORDER items get "PROJECT ORDER"
        if is_finishes:
            cat = f"Surface Finish BOM - {project_name}"
            csv_section(cat, subtask_of=top_name, vendor='Surface Finish BOM')
            hierarchy.get_or_create(cat, top_name, notes=f"Surface Finish BOM for {project_name}\nDropbox: {dropbox_url}\nProject: {asana_url}")
            if effective_vendor:
                vg = f"{effective_vendor} - {project_name}"
                csv_section(vg, subtask_of=cat, vendor=effective_vendor)
                hierarchy.get_or_create(vg, cat, add_to_project=True,
                                      notes=f"{effective_vendor} ordering tasks for {project_name}\nDropbox: {dropbox_url}\nProject: {asana_url}",
                                      custom_fields={'854127216887083': effective_vendor})
                subtask_of = vg
            else:
                subtask_of = cat
        elif effective_vendor:
            if cost_center:
                cc = f"{cost_center} BOM - {project_name}"
                csv_section(cc, subtask_of=top_name, vendor=cost_center)
                hierarchy.get_or_create(cc, top_name, notes=f"{cost_center} BOM for {project_name}\nDropbox: {dropbox_url}\nProject: {asana_url}")
                vg = f"{effective_vendor} - {project_name}"
                csv_section(vg, subtask_of=cc, vendor=effective_vendor)
                hierarchy.get_or_create(vg, cc, add_to_project=True,
                                      notes=f"{effective_vendor} ordering tasks for {project_name}\nDropbox: {dropbox_url}\nProject: {asana_url}",
                                      custom_fields={'854127216887083': effective_vendor})
                subtask_of = vg
            else:
                vg = f"{effective_vendor} - {project_name}"
                csv_section(vg, subtask_of=top_name, vendor=effective_vendor)
                hierarchy.get_or_create(vg, top_name, add_to_project=True,
                                      notes=f"{effective_vendor} ordering tasks for {project_name}\nDropbox: {dropbox_url}\nProject: {asana_url}",
                                      custom_fields={'854127216887083': effective_vendor})
                subtask_of = vg
        elif cost_center:
            cc = f"{cost_center} BOM - {project_name}"
            csv_section(cc, subtask_of=top_name, vendor=cost_center)
            hierarchy.get_or_create(cc, top_name, add_to_project=True, notes=f"{cost_center} BOM for {project_name}\nDropbox: {dropbox_url}\nProject: {asana_url}")
            subtask_of = cc
        else:
            subtask_of = top_name

        parent_gid = hierarchy._gid_cache.get(subtask_of,
                     hierarchy._gid_cache.get(top_name, ''))

        # ---------------------------------------------------------------
        # Add part task to CSV manifest
        # ---------------------------------------------------------------
        order_qty_out = qty_str if section == 'ORDER' else ''
        # ORDER tasks: no assignee (purchasing team handles via vendor groups)
        # PICK tasks: always assigned to purchasing for stock pulling
        assignee_out  = '' if section == 'ORDER' else PURCHASING_EMAIL
        csv_rows.append(_csv_row(task_name, notes=task_notes, subtask_of=subtask_of,
                                 order_qty=order_qty_out, vendor=effective_vendor,
                                 assignee=assignee_out, order_pick=section,
                                 stock_action=stock_action))

        # In dry-run mode populate a DRY placeholder so downstream stages see a value.
        if not commit:
            csv_rows[-1]['Asana Task GID'] = f'DRY_{len(csv_rows)-1:04d}'
            csv_rows[-1]['Asana Task URL'] = ''

        # ---------------------------------------------------------------
        # API creation (commit mode only)
        # ---------------------------------------------------------------
        if commit:
            try:
                # 1. Create task
                task_data = updater.create_subtask(parent_gid, task_name, notes=task_notes, assignee=assignee_out)
                t_gid = task_data.get('gid', '')
                
                # 2. Add to Parts Control (1211660707080749) if QTY > 0
                qty_val = float(qty_str) if qty_str and _is_numeric(qty_str) else 0.0
                if t_gid and qty_val > 0:
                    try:
                        updater.add_to_project(t_gid, '1211660707080749')
                        
                        # 3. Set custom fields (only possible after adding to project)
                        c_fields = {}
                        if qty_val > 0:
                            c_fields['1201447044882111'] = int(qty_val)  # ORDER QTY
                        if effective_vendor:
                            c_fields['854127216887083'] = effective_vendor  # Vendor
                        if pn:
                            c_fields['1213228090549470'] = pn  # Sesame Part No (Full PN)
                        
                        updater.update_task(t_gid, custom_fields=c_fields)
                        
                        # 4. Set Stock Action Enum (1199111659373956)
                        if stock_action == 'PLEASE PICK':
                            updater.update_task(t_gid, custom_fields={'1199111659373956': '1205843231438999'})
                        else:
                            updater.update_task(t_gid, custom_fields={'1199111659373956': '1214069309221719'})

                    except Exception as e:
                        print(f"    [WARN] Parts Control mapping failed for '{task_name[:60]}': {e}")
                
                url = task_url(project_gid, t_gid)
                print(f"    [CREATED] '{task_name[:60]}' -> {url}")
                # Record GID/URL in the CSV manifest for downstream stages
                if csv_rows:
                    try:
                        csv_rows[-1]['Asana Task GID'] = t_gid
                        csv_rows[-1]['Asana Task URL'] = url
                    except Exception:
                        pass
                bom_status = 'in_stock' if section == 'PICK' else 'needs_order'
                try:
                    result = write_bom_requirement({
                        'build_ref':           project_name,
                        'asana_project_gid':   project_gid,
                        'sesame_part_no':      pn,
                        'part_description':    desc,
                        'required_qty':        bom_qty,
                        'stock_qty_at_import': None,
                        'status':              bom_status,
                        'asana_task_gid':      t_gid,
                        'source_bom_file':     source,
                    })
                    if result.get('written'):
                        print(f"    [DB] bom_requirements: {pn} ({bom_status})")
                except Exception as e:
                    print(f"    [WARN] bom_requirements write failed for '{pn}': {e}")
                time.sleep(pause)
            except Exception as e:
                print(f"    [ERROR] '{task_name[:60]}' -- {e}")
        else:
            print(f"    [DRY] Would create: '{task_name[:80]}'")
            bom_status = 'in_stock' if section == 'PICK' else 'needs_order'
            print(f"    [DRY] Would write bom_requirements: pn={pn}, qty={bom_qty}, status={bom_status}")

    return csv_rows


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Stage 5 — BOM Import to Asana v4')
    parser.add_argument('--input', '-i', required=True, help='BOM_Staging_Review xlsx (Stage 4 output)')
    parser.add_argument('--project-name', required=True, help='Project name for task hierarchy, e.g. "1501 - Downing Street"')
    parser.add_argument('--dropbox-url', default='', help='Dropbox folder URL')
    parser.add_argument('--asana-url', default='', help='Asana project task URL')
    parser.add_argument('--project-gid', default=DEFAULT_PROJECT_GID, help=f'Asana project GID (hardcoded to Build Orders: {DEFAULT_PROJECT_GID})')
    parser.add_argument('--sheet', default='BOM Review', help='Sheet name in the staging Excel')
    parser.add_argument('--output', '-o', default=None, help='Output manifest CSV (default: task_manifest_YYYYMMDD.csv)')
    parser.add_argument('--commit', action='store_true', help='Actually create tasks in Asana (default: dry-run)')
    parser.add_argument('--pause', type=float, default=0.3, help='Seconds between API calls (default 0.3)')
    parser.add_argument('--golden-index', default='../2.Stock Controls/Merged_Golden_Index_Rev4.csv', help='Path to Golden Index for amendment checks')
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input not found: {input_path}")
        sys.exit(1)

    date_str = datetime.now().strftime('%Y%m%d')
    output_path = Path(args.output) if args.output else Path(f'task_manifest_{date_str}.csv')

    print(f"Loading staging Excel: {input_path}")
    df = pd.read_excel(input_path, sheet_name=args.sheet, dtype=str).fillna('')
    print(f"  {len(df)} rows loaded from sheet '{args.sheet}'")

    # Extract KAN stock URLs from the 'Asana Task URL' hyperlink column (openpyxl only)
    kan_urls: dict[int, str] = {}
    try:
        from openpyxl import load_workbook as _lw
        _wb = _lw(input_path, data_only=True)
        _ws = _wb[args.sheet]
        # Normalise header names and find the Asana Task URL column more robustly.
        def _normalize_header(value):
            if value is None:
                return ''
            header = str(value).strip().split('\n')[0]
            return ' '.join(header.lower().split())

        _headers = [_normalize_header(c.value) for c in _ws[1]]
        target_names = {'asana task url', 'asana task url (hyperlink)', 'asana_url', 'asana task url hyperlink'}
        _url_idx = next((i for i, h in enumerate(_headers) if h in target_names), None)
        if _url_idx is not None:
            for _row_obj in _ws.iter_rows(min_row=2):
                _cell = _row_obj[_url_idx]
                if _cell.hyperlink and _cell.hyperlink.target:
                    _df_row = _cell.row - 2   # 0-based pandas index
                    kan_urls[_df_row] = _cell.hyperlink.target
        print(f"  {len(kan_urls)} KAN stock URL(s) extracted")
    except Exception as _e:
        print(f"  WARNING: Could not extract KAN URLs: {_e}")

    if not args.commit:
        print("\n[DRY-RUN MODE] No Asana tasks will be created. Use --commit to write.\n")

    # In dry-run mode we never call the API, so token is not required
    try:
        updater = AsanaUpdater()
    except RuntimeError:
        if args.commit:
            raise
        updater = None  # type: ignore

    manifest = process(
        df,
        project_name=args.project_name,
        dropbox_url=args.dropbox_url,
        asana_url=args.asana_url,
        project_gid=args.project_gid,
        updater=updater,
        commit=args.commit,
        pause=args.pause,
        kan_urls=kan_urls,
        golden_index_path=args.golden_index,
    )

    # Write Asana-importable CSV (column order matches reference format)
    CSV_COLS = ['Name', 'Description', 'Subtask of', 'ORDER QTY', 'Vendor', 'Assignee', 'Email', 'Due Date', 'ORDER_PICK', 'Stock Action', 'Asana Task GID', 'Asana Task URL']
    manifest_df = pd.DataFrame(manifest, columns=CSV_COLS)
    manifest_df.to_csv(output_path, index=False)

    order_rows = manifest_df['ORDER QTY'].ne('').sum()
    pick_rows  = manifest_df[(manifest_df['ORDER QTY'] == '') & manifest_df['Assignee'].ne('')].shape[0]
    part_rows  = order_rows + pick_rows
    hier_rows  = len(manifest_df) - part_rows

    print(f"\n{'='*60}")
    print(f"  Total CSV rows : {len(manifest_df)}  ({hier_rows} hierarchy + {part_rows} parts)")
    print(f"  ORDER parts    : {order_rows}")
    print(f"  PICK parts     : {pick_rows}")
    print(f"  Manifest saved : {output_path}")
    if not args.commit:
        print("  (DRY-RUN — rerun with --commit to write to Asana)")
    print(f"{'='*60}")


if __name__ == '__main__':
    main()
