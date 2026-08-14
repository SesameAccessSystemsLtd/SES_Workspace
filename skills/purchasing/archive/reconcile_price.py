import requests
import argparse
import sys
import json
import re

ASANA_TOKEN = "***REMOVED_ASANA_PAT***"
HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}", "Accept": "application/json"}

# GIDs
GILES_GID = "369446862164512"
ORDER_VALUE_GID = "1204913986258520"
PRICE_EX_VAT_GID = "854330340727040"  # Line item unit price
ORDER_QTY_GID = "1201447044882111"

def extract_price(text):
    """Try to extract a price from email text."""
    m = re.search(r"[A$]\s?([\d,]+\.?\d{0,2})", text)
    if m:
        return float(m.group(1).replace(",", ""))
    m = re.search(r"([\d,]+\.?\d{2})\s*(?:ex\s*vat|excl|net|GBP)", text, re.IGNORECASE)
    if m:
        return float(m.group(1).replace(",", ""))
    return None

def reconcile_po_price(po_task_gid, extracted_price, commit=False):
    print(f"Fetching PO Task {po_task_gid}...")
    r = requests.get(f"https://app.asana.com/api/1.0/tasks/{po_task_gid}?opt_fields=name,custom_fields.name,custom_fields.display_value,dependents", headers=HEADERS)
    if r.status_code != 200:
        print(f"Error fetching PO task: {r.text}")
        return

    task_data = r.json()['data']
    po_name = task_data['name']
    
    current_order_val = 0.0
    for cf in task_data.get('custom_fields', []):
        if cf.get('gid') == ORDER_VALUE_GID and cf.get('display_value'):
            try:
                current_order_val = float(cf.get('display_value').replace(',', ''))
            except ValueError:
                pass

    dependents = [d['gid'] for d in task_data.get('dependents', [])]
    sum_of_lines = 0.0
    
    print(f"\n--- RECONCILIATION REPORT ---")
    print(f"PO Task: {po_name}")
    print(f"Extracted Email Price: {extracted_price:.2f}")
    print(f"Current Asana ORDER Value: {current_order_val:.2f}")
    print(f"Dependent Line Items: {len(dependents)}")
    
    for dep_gid in dependents:
        dep_r = requests.get(f"https://app.asana.com/api/1.0/tasks/{dep_gid}?opt_fields=name,custom_fields", headers=HEADERS)
        if dep_r.status_code == 200:
            dep_data = dep_r.json()['data']
            qty = 0.0
            unit_price = 0.0
            for cf in dep_data.get('custom_fields', []):
                if cf.get('gid') == ORDER_QTY_GID and cf.get('display_value'):
                    try: qty = float(cf.get('display_value'))
                    except ValueError: pass
                if cf.get('gid') == PRICE_EX_VAT_GID and cf.get('display_value'):
                    try: unit_price = float(cf.get('display_value').replace('', '').replace(',', ''))
                    except ValueError: pass
            sum_of_lines += (qty * unit_price)

    print(f"Sum of Dependent Line Items: {sum_of_lines:.2f}")
    
    action_update_po = False
    comment_html = ""

    # EVALUATION LOGIC
    if current_order_val == 0.0:
        action_update_po = True
        if sum_of_lines > 0:
            if abs(extracted_price - sum_of_lines) < 0.01: # Float safety
                comment_html = f"<body><a data-asana-gid=\"{GILES_GID}\">Giles</a> Quote/Order received for {extracted_price:.2f}. I have updated the PO total. This perfectly matches the sum of the line items.</body>"
            elif extracted_price > sum_of_lines:
                comment_html = f"<body><a data-asana-gid=\"{GILES_GID}\">Giles</a> Quote/Order received for {extracted_price:.2f}. I updated the PO total. Note: This is higher than the sum of the line items ({sum_of_lines:.2f}). This may be due to carriage, additional items, or missing line item prices. Please review.</body>"
            else:
                comment_html = f"<body><strong>WARNING:</strong> <a data-asana-gid=\"{GILES_GID}\">Giles</a> Quote/Order received for {extracted_price:.2f}. I updated the PO total, but this is LOWER than the sum of your line items ({sum_of_lines:.2f}). Please review.</body>"
        else:
            comment_html = f"<body><a data-asana-gid=\"{GILES_GID}\">Giles</a> Quote/Order received for {extracted_price:.2f}. I updated the PO total. The line items do not currently have unit prices.</body>"
            
    else:
        if abs(current_order_val - extracted_price) < 0.01:
            comment_html = f"<body><a data-asana-gid=\"{GILES_GID}\">Giles</a> Order confirmation received. Total price ({extracted_price:.2f}) matches Asana perfectly.</body>"
        else:
            comment_html = f"<body><strong>WARNING:</strong> <a data-asana-gid=\"{GILES_GID}\">Giles</a> Order confirmation received for {extracted_price:.2f}, but the Asana ORDER Value is currently {current_order_val:.2f}. Please review the discrepancy.</body>"

    print("\n--- ACTIONS ---")
    if action_update_po:
        print(f"ACTION: Update PO 'ORDER Value' to {extracted_price:.2f}")
        if commit:
            payload = {"data": {"custom_fields": {ORDER_VALUE_GID: extracted_price}}}
            res = requests.put(f"https://app.asana.com/api/1.0/tasks/{po_task_gid}", headers=HEADERS, json=payload)
            if res.status_code == 200:
                print("  [SUCCESS] PO Updated.")
            else:
                print(f"  [ERROR] Failed to update PO: {res.text}")
    else:
        print("ACTION: None (PO Value already exists).")
        
    print(f"COMMENT: {comment_html}")
    if commit and comment_html:
        payload = {"data": {"html_text": comment_html}}
        res = requests.post(f"https://app.asana.com/api/1.0/tasks/{po_task_gid}/stories", headers=HEADERS, json=payload)
        if res.status_code in [200, 201]:
            print("  [SUCCESS] Comment posted.")
        else:
            print(f"  [ERROR] Failed to post comment: {res.text}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Reconcile PO Price against line items.")
    parser.add_argument("--task-gid", required=True, help="The GID of the PO Task")
    parser.add_argument("--price", required=True, type=float, help="The extracted price from the email")
    parser.add_argument("--commit", action="store_true", help="Actually apply updates to Asana")
    
    args = parser.parse_args()
    
    if not args.commit:
        print("--- DRY RUN MODE (Use --commit to execute) ---")
        
    reconcile_po_price(args.task_gid, args.price, args.commit)
