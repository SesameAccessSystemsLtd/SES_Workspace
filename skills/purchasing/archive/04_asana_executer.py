import os
import json
import requests
from dotenv import load_dotenv
import re
import datetime

load_dotenv(r"C:\Users\GilesHugo\.openclaw\.env", override=True)
load_dotenv(r"C:\Users\GilesHugo\.openclaw\workspace\.env", override=True)

ASANA_PERSONAL_ACCESS_TOKEN = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
ASANA_HEADERS = {"Authorization": f"Bearer {ASANA_PERSONAL_ACCESS_TOKEN}", "Accept": "application/json"}

MS_GRAPH_CLIENT_ID = os.getenv("Purchaisng_secret_ID")
MS_GRAPH_CLIENT_SECRET = os.getenv("purchasing_Email_Api_Key")
MS_GRAPH_TENANT_ID = os.getenv("Purchasing_tenant_ID")
MS_GRAPH_SENDER_EMAIL = os.getenv("AGENT_NOTIFY_EMAIL", "purchasing@sesameaccess.com")

# Asana GIDs from MEMORY.md (auto-injected and read from .env)
ASANA_PRICE_EX_VAT_FIELD_GID = os.getenv("PRICE_EX_VAT_CUSTOM_FIELD_GID", "854330340727040")
ASANA_DELIVERY_DATE_FIELD_GID = os.getenv("DELIVERY_DATE_CUSTOM_FIELD_GID", "1208706270294053")
ASANA_ORDER_STATUS_FIELD_GID = os.getenv("ORDER_STATUS_CUSTOM_FIELD_GID", "1143955334207040")
ASANA_ORDER_STATUS_CONFIRMED_GID = os.getenv("ORDER_STATUS_CONFIRMED_ENUM_GID", "1143955334207043")

if not ASANA_PERSONAL_ACCESS_TOKEN: raise ValueError("ASANA_PERSONAL_ACCESS_TOKEN not found in environment variables.")
if not all([MS_GRAPH_CLIENT_ID, MS_GRAPH_CLIENT_SECRET, MS_GRAPH_TENANT_ID, MS_GRAPH_SENDER_EMAIL]):
    print(f"DEBUG: MS_GRAPH_CLIENT_ID (script): {MS_GRAPH_CLIENT_ID}")
    print(f"DEBUG: MS_GRAPH_CLIENT_SECRET (script): {MS_GRAPH_CLIENT_SECRET}")
    print(f"DEBUG: MS_GRAPH_TENANT_ID (script): {MS_GRAPH_TENANT_ID}")
    print(f"DEBUG: MS_GRAPH_SENDER_EMAIL (script): {MS_GRAPH_SENDER_EMAIL}")
    raise ValueError("One or more MS Graph API credentials not found in environment variables.")

PENDING_APPROVAL_QUEUE_PATH = r"C:\Users\GilesHugo\.openclaw\workspace\data\pending_approval_queue.json"

# --- MS Graph API Authentication and Utility Functions ---
MS_GRAPH_TOKEN_URL = f"https://login.microsoftonline.com/{MS_GRAPH_TENANT_ID}/oauth2/v2.0/token"
MS_GRAPH_SCOPE = "https://graph.microsoft.com/.default"

def get_graph_access_token():
    token_data = {
        "client_id": MS_GRAPH_CLIENT_ID,
        "client_secret": MS_GRAPH_CLIENT_SECRET,
        "scope": MS_GRAPH_SCOPE,
        "grant_type": "client_credentials",
    }
    r = requests.post(MS_GRAPH_TOKEN_URL, data=token_data)
    r.raise_for_status()
    return r.json()["access_token"]

def make_graph_request(method, url, access_token, json_payload=None):
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    if method == 'PATCH':
        r = requests.patch(url, headers=headers, json=json_payload)
    else: # GET or POST
        r = requests.request(method, url, headers=headers, json=json_payload)
    r.raise_for_status()
    return r

def tag_email_as_processed(email_id, access_token, category_name="Bot Processed onto Asana"):
    try:
        # First, get existing categories to avoid overwriting
        get_url = f"https://graph.microsoft.com/v1.0/users/{MS_GRAPH_SENDER_EMAIL}/messages/{email_id}?=categories"
        get_r = make_graph_request("GET", get_url, access_token)
        existing_categories = get_r.json().get("categories", [])

        if category_name not in existing_categories:
            existing_categories.append(category_name)
            patch_url = f"https://graph.microsoft.com/v1.0/users/{MS_GRAPH_SENDER_EMAIL}/messages/{email_id}"
            make_graph_request("PATCH", patch_url, access_token, json_payload={"categories": existing_categories})
            print(f"  -> Tagged email {email_id} with '{category_name}'.")
        else:
            print(f"  -> Email {email_id} already tagged with '{category_name}'.")
    except Exception as e:
        print(f"  -> Error tagging email {email_id}: {e}")

def apply_asana_update(item, graph_access_token):
    task_gid = item.get("asana_task_gid")
    if not task_gid: return # Skip if no task is linked

    proposed_action_str = item.get("proposed_action", "")
    email_subject = item.get("subject", "")
    email_id = item.get("email_id")
    
    updates = {}
    comment_html_parts = [f"<body><strong>Bot Update:</strong> Processed email '{email_subject}'."]

    # Asana Task Updates (using raw requests)
    # Fetch current task to compare dates / prices etc.
    task_url = f"https://app.asana.com/api/1.0/tasks/{task_gid}?opt_fields=due_on,custom_fields.gid,custom_fields.number_value,custom_fields.enum_value.gid"
    r_task = requests.get(task_url, headers=ASANA_HEADERS)
    r_task.raise_for_status()
    current_task_data = r_task.json().get("data", {})

    current_due_on = current_task_data.get("due_on")
    current_price_ex_vat = None
    current_order_status_gid = None

    for cf in current_task_data.get('custom_fields', []):
        if cf.get('gid') == ASANA_PRICE_EX_VAT_FIELD_GID: current_price_ex_vat = cf.get('number_value')
        if cf.get('gid') == ASANA_ORDER_STATUS_FIELD_GID and cf.get('enum_value'): current_order_status_gid = cf['enum_value'].get('gid')

    # Parse proposed actions from the string
    actions = [a.strip() for a in proposed_action_str.split(';') if a.strip()]

    for action in actions:
        if "Update 'Price Ex VAT' to" in action:
            price_match = re.search(r'Update \'Price Ex VAT\' to ([\d.]+)', action)
            if price_match: 
                new_price = float(price_match.group(1))
                if current_price_ex_vat is None or abs(current_price_ex_vat - new_price) > 0.01: # Only update if different
                    if "custom_fields" not in updates: updates["custom_fields"] = {}
                    updates["custom_fields"][ASANA_PRICE_EX_VAT_FIELD_GID] = new_price
                    comment_html_parts.append(f"<br>Updated 'Price Ex VAT' to {new_price}.")
                else:
                    comment_html_parts.append(f"<br>'Price Ex VAT' already matches ({new_price}).")
            
        elif "Update delivery date to" in action:
            date_match = re.search(r'Update delivery date to (\d{4}-\d{2}-\d{2})', action)
            if date_match:
                new_date_str = date_match.group(1)
                # Compare with current_due_on
                if current_due_on is None or datetime.datetime.strptime(new_date_str, '%Y-%m-%d').date() < datetime.datetime.strptime(current_due_on, '%Y-%m-%d').date():
                    updates["due_on"] = new_date_str
                    comment_html_parts.append(f"<br>Updated Due Date to {new_date_str}.")
                else:
                    comment_html_parts.append(f"<br>Skipped Due Date update: Asana date ({current_due_on}) is later or same.")

        elif "Propose changing ORDER Status to 'On Order - Delivery Date Confirmed'" in action:
            if current_order_status_gid != ASANA_ORDER_STATUS_CONFIRMED_GID:
                if "custom_fields" not in updates: updates["custom_fields"] = {}
                updates["custom_fields"][ASANA_ORDER_STATUS_FIELD_GID] = ASANA_ORDER_STATUS_CONFIRMED_GID
                comment_html_parts.append(f"<br>Proposed ORDER Status: 'On Order - Delivery Date Confirmed'.")
            else:
                comment_html_parts.append(f"<br>ORDER Status already 'On Order - Delivery Date Confirmed'.")

        elif "Flag issue:" in action:
            issue_summary = action.replace("Flag issue:", "").strip()
            comment_html_parts.append(f"<br><strong>Issue Flagged:</strong> {issue_summary}")
        elif "Task not found." in action: # Only add if it's the primary action, not a note for a matched task
            comment_html_parts.append(f"<br><strong>Note:</strong> {action}")
        
        # Add matched_by to comment (example: (Matched via PO Number in Local DB))
        match_info = re.search(r'\(Matched via.*?\)', action)
        if match_info: comment_html_parts.append(f"<br>{match_info.group(0)}")

    # Apply task updates
    if updates:
        try:
            requests.put(f"https://app.asana.com/api/1.0/tasks/{task_gid}", headers=ASANA_HEADERS, json={"data": updates})
            print(f"  -> Updated Asana task {task_gid}.")
        except requests.exceptions.HTTPError as e:
            print(f"  -> Error updating Asana task {task_gid}: {e.response.text}")

    # Post comment
    comment_payload = {"data": {"html_text": "\".join(comment_html_parts)"}}
    try:
        requests.post(f"https://app.asana.com/api/1.0/tasks/{task_gid}/stories", headers=ASANA_HEADERS, json=comment_payload)
        print(f"  -> Posted comment to Asana task {task_gid}.")
    except requests.exceptions.HTTPError as e:
        print(f"  -> Error posting comment to Asana task {task_gid}: {e.response.text}")
    
    # Tag email in Outlook
    if email_id:
        tag_email_as_processed(email_id, graph_access_token)

def main():
    graph_access_token = get_graph_access_token()
    
    try:
        with open(PENDING_APPROVAL_QUEUE_PATH, 'r') as f:
            queue = json.load(f)
    except FileNotFoundError:
        print("Pending approval queue not found.")
        return

    processed_gids = []
    for item in queue:
        if item.get("status") == "approved" and item.get("asana_task_gid"):
            apply_asana_update(item, graph_access_token)
            processed_gids.append(item["asana_task_gid"])

    # Remove processed items from the queue
    new_queue = [item for item in queue if item.get("asana_task_gid") not in processed_gids]
    with open(PENDING_APPROVAL_QUEUE_PATH, 'w') as f:
        json.dump(new_queue, f, indent=2)
    
    if processed_gids:
        print(f"Successfully processed and cleared {len(processed_gids)} approved items from the queue.")
    else:
        print("No approved items with valid GIDs were found in the queue to process.")

if __name__ == '__main__':
    main()
