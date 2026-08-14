# -*- coding: utf-8 -*-
import os
import re
import sqlite3
import requests
from dotenv import load_dotenv

load_dotenv(r"C:\Users\GilesHugo\.hermes\.env", override=True)

ASANA_TOKEN = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}", "Accept": "application/json"}
PRICE_GID = "854330340727040"
DB_PATH = r"C:\Users\GilesHugo\.openclaw\workspace\db\openclaw.db"

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS active_purchasing_tasks (
        task_gid TEXT PRIMARY KEY,
        name TEXT,
        sqr_number TEXT,
        po_number TEXT,
        price_ex_vat REAL,
        due_on TEXT
    )''')
    conn.commit()
    return conn

def sync_via_search():
    conn = init_db()
    c = conn.cursor()
    
    url = "https://app.asana.com/api/1.0/workspaces/357939803556123/tasks/search"
    params = {
        "projects.any": "848657415032331,854650811917168",
        "completed": "false",
        "is_subtask": "false",
        "opt_fields": "name,due_on,custom_fields.display_value,custom_fields.gid",
        "limit": 100
    }
    
    print("Fetching tasks from Asana...")
    r = requests.get(url, headers=HEADERS, params=params)
    if r.status_code != 200:
        print(f"Error: {r.text}")
        return
        
    tasks = r.json().get("data", [])
    print(f"Found {len(tasks)} active tasks in those projects.")
    
    for t in tasks:
        gid = t['gid']
        name = t['name']
        due_on = t.get('due_on')
        
        sqr_match = re.search(r'(SQR-\d+)', name, re.IGNORECASE)
        sqr_number = sqr_match.group(1).upper() if sqr_match else None
        
        po_match = re.search(r'(209\d{2}(?:-\d+)?)', name)
        po_number = po_match.group(1) if po_match else None
        
        price = None
        for cf in t.get('custom_fields', []):
            if cf['gid'] == PRICE_GID and cf.get('display_value'):
                try:
                    price_str = cf['display_value'].replace(',', '').replace('$', '').strip()
                    # Strip out unicode currency symbols manually to avoid python encoding fuss
                    price_str = ''.join(c for c in price_str if c.isdigit() or c == '.')
                    price = float(price_str)
                except ValueError:
                    pass
        
        c.execute('''
            INSERT INTO active_purchasing_tasks (task_gid, name, sqr_number, po_number, price_ex_vat, due_on)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_gid) DO UPDATE SET
                name=excluded.name,
                sqr_number=excluded.sqr_number,
                po_number=excluded.po_number,
                price_ex_vat=excluded.price_ex_vat,
                due_on=excluded.due_on
        ''', (gid, name, sqr_number, po_number, price, due_on))
    
    conn.commit()
    conn.close()
    print("Database sync complete.")

if __name__ == '__main__':
    sync_via_search()
