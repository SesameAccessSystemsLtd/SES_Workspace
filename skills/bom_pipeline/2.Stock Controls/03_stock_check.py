"""
Stage 3 -- Stock Check
======================
Pulls live stock levels from Asana for each KAN-matched part using the dedicated
KAN custom field (GID: 1206590288351824) for exact task lookup.

ALL BOM rows are maintained in the output:
  - KAN-matched rows: get live stock data + ORDER_PICK classification
  - Unmatched rows: ORDER_PICK = ORDER, stock columns blank

ORDER_PICK logic:
  PICK  -- Stock QTY >= BOM item qty (can fulfil from current stock)
  ORDER -- Stock QTY < BOM item qty OR no KAN match (must purchase)

Low Stock Alert = YES if remaining < Trigger Order QTY (flag to replenish even if PICK)

Usage:
    python 03_stock_check.py --input bom_with_kan.csv --output bom_stock_check.csv
    python 03_stock_check.py --input bom_with_kan.csv --commit

Custom field GIDs (KANBAN project):
    KAN              : 1206590288351824
    STOCK QTY        : 1126782469949969
    Trigger Order QTY: 1198179728033183   <-- read for the Low Stock threshold
"""

import argparse
import sys
import time
from pathlib import Path

import requests
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from asana_updater import AsanaUpdater

KAN_FIELD_GID       = '1206590288351824'
STOCK_QTY_GID       = '1126782469949969'
TRIGGER_QTY_GID     = '1198179728033183'   # Asana field is "Trigger Order QTY" — the replenish (low-stock) threshold
KANBAN_PROJECT_GID  = '632603478911321'
TASK_BASE_URL       = f'https://app.asana.com/0/{KANBAN_PROJECT_GID}'


def order_or_pick(stock_qty: float, bom_qty: float) -> str:
    return 'PICK' if stock_qty >= bom_qty else 'ORDER'


def find_task_by_kan(updater, workspace_gid, kan, pause):
    """Search KANBAN by KAN custom field value. Returns first match dict or {}."""
    params = {
        f'custom_fields.{KAN_FIELD_GID}.value': kan,
        'projects.any': KANBAN_PROJECT_GID,
        'opt_fields': 'name,gid,notes,permalink_url',
        'limit': 5,
    }
    r = requests.get(f"{updater.base}/workspaces/{workspace_gid}/tasks/search",
                     headers=updater.headers, params=params)
    r.raise_for_status()
    time.sleep(pause)
    tasks = r.json().get('data', [])
    return tasks[0] if tasks else {}


def _empty(status):
    return {
        'Asana Task GID': '', 'Asana Task URL': '', 'Asana Task Name': '',
        'Asana Task Description': '', 'Stock QTY': '', 'Trigger Order QTY': '',
        'Remaining after BOM': '', 'Low Stock Alert': '',
        'ORDER_PICK': 'ORDER', 'Stock Check Status': status,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--input', '-i', required=True)
    parser.add_argument('--output', '-o', default='bom_stock_check.csv')
    parser.add_argument('--qty-col', default='item qty')
    parser.add_argument('--commit', action='store_true')
    parser.add_argument('--pause', type=float, default=0.3)
    args = parser.parse_args()

    bom = pd.read_csv(args.input, dtype=str).fillna('')
    print(f"Loaded {len(bom)} rows from {args.input}")

    qty_col = next((c for c in bom.columns if c.lower() == args.qty_col.lower()), None)
    if not qty_col:
        print(f"ERROR: Qty column '{args.qty_col}' not found.")
        sys.exit(1)

    updater = AsanaUpdater()
    ws_resp = requests.get(f"{updater.base}/users/me", headers=updater.headers)
    ws_resp.raise_for_status()
    workspace_gid = ws_resp.json()['data']['workspaces'][0]['gid']

    kan_cache = {}
    rows_out = []
    totals = {'PICK': 0, 'ORDER': 0, 'ERROR': 0}

    for idx, row in bom.iterrows():
        match_status = str(row.get('Index Match Status', '')).strip()
        kan = str(row.get('Mapped KAN', '')).strip()

        try:
            bom_qty = float(str(row.get(qty_col, '0')).strip())
        except ValueError:
            bom_qty = 0.0

        if match_status == 'UNMATCHED' or not kan:
            rows_out.append(_empty('UNMATCHED'))
            totals['ORDER'] += 1
            continue

        if kan not in kan_cache:
            try:
                kan_cache[kan] = find_task_by_kan(updater, workspace_gid, kan, args.pause)
            except Exception as e:
                kan_cache[kan] = {'_error': str(e)}

        task = kan_cache[kan]
        if not task or '_error' in task:
            err = task.get('_error', 'NOT_FOUND') if task else 'NOT_FOUND'
            rows_out.append(_empty(f'NOT_FOUND: {err}'))
            totals['ORDER'] += 1
            print(f"  [NOT FOUND] Row {idx+2}: {kan}")
            continue

        gid        = task.get('gid', '')
        task_name  = task.get('name', '')
        task_notes = (task.get('notes') or '')[:300].replace('\n', ' ')
        task_url   = f"{TASK_BASE_URL}/{gid}"

        try:
            full = updater.get_task(gid, opt_fields='custom_fields,name')
            time.sleep(args.pause)
        except Exception as e:
            rows_out.append({**_empty(f'API_ERROR: {e}'),
                             'Asana Task GID': gid, 'Asana Task URL': task_url,
                             'Asana Task Name': task_name, 'Asana Task Description': task_notes})
            totals['ERROR'] += 1
            continue

        stock_qty = AsanaUpdater.extract_custom_field_value(full, STOCK_QTY_GID)
        trig_qty  = AsanaUpdater.extract_custom_field_value(full, TRIGGER_QTY_GID)
        stock_qty = float(stock_qty) if stock_qty is not None else 0.0
        trig_qty  = float(trig_qty)  if trig_qty  is not None else 0.0

        remaining = stock_qty - bom_qty
        low_alert = 'YES' if remaining < trig_qty else 'NO'
        op        = order_or_pick(stock_qty, bom_qty)

        status = ''
        if args.commit and op == 'ORDER':
            try:
                updater.update_task(gid, completed=False)
                status = 'UNCOMPLETED'
            except Exception as e:
                status = f'WRITE_ERROR: {e}'

        rows_out.append({
            'Asana Task GID':         gid,
            'Asana Task URL':         task_url,
            'Asana Task Name':        task_name,
            'Asana Task Description': task_notes,
            'Stock QTY':              stock_qty,
            'Trigger Order QTY':      trig_qty,
            'Remaining after BOM':    remaining,
            'Low Stock Alert':        low_alert,
            'ORDER_PICK':             op,
            'Stock Check Status':     status,
        })
        totals[op] += 1
        print(f"  [{op:5}] {task_name[:38]:40} | "
              f"Stk={stock_qty:.0f} BOM={bom_qty:.0f} "
              f"Rem={remaining:.0f} Trig={trig_qty:.0f} LowAlert={low_alert}")

    result_df = pd.DataFrame(rows_out)
    for col in result_df.columns:
        bom[col] = result_df[col].values

    bom.to_csv(args.output, index=False)

    print(f"\n{'='*55}")
    print(f"  Total rows : {len(bom)}")
    print(f"  PICK       : {totals['PICK']}")
    print(f"  ORDER      : {totals['ORDER']}")
    if totals['ERROR']:
        print(f"  ERRORS     : {totals['ERROR']}")
    print(f"  Output     : {args.output}")
    print(f"{'='*55}")


if __name__ == '__main__':
    main()
