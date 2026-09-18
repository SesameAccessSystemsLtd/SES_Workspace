import requests
import argparse
import sys
import os

# 2026-09-17: hardcoded token fallback removed (leaked in git history; rotate it).
#
# A bare os.environ.get() default is a trap: the module still imports and runs
# happily with a *revoked* token, and every request then fails as a confusing
# 401 rather than "you forgot to set the token". Fail loudly at import instead.
ASANA_TOKEN = (os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN") or "").strip()
if not ASANA_TOKEN:
    raise RuntimeError(
        "ASANA_PERSONAL_ACCESS_TOKEN not set. Export it (or put it in "
        "C:/Users/GilesHugo/.hermes/.env) before running. Do NOT hardcode it."
    )
HEADERS = {"Authorization": f"Bearer {ASANA_TOKEN}", "Accept": "application/json"}

SUPPLIER_PROJECT = "414986125671548"
VENDOR_FIELD = "854127216887083"
STOCK_ACTION_FIELD = "1199111659373956"
ORDER_FOR_STOCK_ENUM = "1199111659373959"

# New tags for specific conditions
SURFACE_FINISH_TAG_GID = "954138430192867"
BEARING_SUPPLIER_TAG_GID = "1201204394317700"
SURFACE_FINISH_KEYWORDS = ['galvanising', 'marine lacquer', 'yellow zinc plating', 'electroplating']

def get_supplier_list():
    suppliers = []
    r = requests.get(f"https://app.asana.com/api/1.0/projects/{SUPPLIER_PROJECT}/tasks?opt_fields=name,tags.name,tags.gid", headers=HEADERS)
    if r.status_code == 200:
        return r.json().get('data', [])
    return suppliers

def add_tag_to_task(task_gid, tag_gid, dry_run):
    if tag_gid:
        if dry_run:
            print(f"  - [DRY RUN] Would add tag: {tag_gid}")
        else:
            add_r = requests.post(f"https://app.asana.com/api/1.0/tasks/{task_gid}/addTag", headers=HEADERS, json={"data": {"tag": tag_gid}})
            if add_r.status_code == 200:
                print(f"    -> Added tag {tag_gid}")
            else:
                print(f"    -> Failed to add tag {tag_gid}: {add_r.text}")

def process_task(task_gid, suppliers, dry_run=True):
    r = requests.get(f"https://app.asana.com/api/1.0/tasks/{task_gid}?opt_fields=name,custom_fields,tags", headers=HEADERS)
    if r.status_code != 200:
        print(f"Error fetching task {task_gid}: {r.text}")
        return
        
    task_data = r.json().get('data', {})
    task_name = task_data.get('name')
    print(f"\nProcessing Task: {task_name} ({task_gid}) (URL: https://app.asana.com/0/{SUPPLIER_PROJECT}/{task_gid})")
    
    vendor_name = None
    stock_action_enum = None
    existing_tag_gids = [t.get('gid') for t in task_data.get('tags', [])]
    
    for cf in task_data.get('custom_fields', []):
        if cf.get('gid') == VENDOR_FIELD:
            vendor_name = cf.get('display_value')
        if cf.get('gid') == STOCK_ACTION_FIELD:
            enum_val = cf.get('enum_value')
            if enum_val:
                stock_action_enum = enum_val.get('gid')
                
    if stock_action_enum == ORDER_FOR_STOCK_ENUM and vendor_name:
        vendor_name_lower = vendor_name.lower()
        tags_to_add_gids = []

        # Rule 1: Dynamic supplier list match
        match = None
        for sup in suppliers:
            if vendor_name_lower in sup.get('name', '').lower() or sup.get('name', '').lower() in vendor_name_lower:
                match = sup
                break
                
        if match:
            print(f"  - Matched dynamic Supplier: {match.get('name')}")
            for t_gid in [t.get('gid') for t in match.get('tags', [])]:
                if t_gid not in existing_tag_gids:
                    tags_to_add_gids.append(t_gid)
        
        # Rule 2: Surface Finish Suppliers
        elif any(keyword in vendor_name_lower for keyword in SURFACE_FINISH_KEYWORDS):
            print(f"  - Matched Surface Finish Supplier keyword: '{vendor_name}'")
            if SURFACE_FINISH_TAG_GID not in existing_tag_gids:
                tags_to_add_gids.append(SURFACE_FINISH_TAG_GID)

        # Rule 3: Bearing Supplier
        elif vendor_name_lower == 'bearing supplier':
            print(f"  - Matched Bearing Supplier: '{vendor_name}'")
            if BEARING_SUPPLIER_TAG_GID not in existing_tag_gids:
                tags_to_add_gids.append(BEARING_SUPPLIER_TAG_GID)
        
        if tags_to_add_gids:
            print(f"  - Tags to add: {tags_to_add_gids}")
            for t_gid in tags_to_add_gids:
                add_tag_to_task(task_gid, t_gid, dry_run)
        else:
            print("  - No new tags to add (already present or no matching rule).")

    else:
        print(f"  - Ignored: Stock Action is not 'Order for Stock' or Vendor is empty. (Vendor: {vendor_name}, Stock Action: {stock_action_enum})")

def process_project(project_gid, suppliers, dry_run=True):
    print(f"Fetching incomplete tasks for project {project_gid}...\r\n(Using ASANA_TOKEN from environment variable or hardcoded if not found.)")
    r = requests.get(f"https://app.asana.com/api/1.0/projects/{project_gid}/tasks?completed_since=now&opt_fields=gid", headers=HEADERS)
    if r.status_code == 200:
        tasks = r.json().get('data', [])
        print(f"Found {len(tasks)} incomplete tasks. Processing...")
        for t in tasks:
            process_task(t.get('gid'), suppliers, dry_run)
    else:
        print(f"Error fetching project tasks: {r.text}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Auto-tag Asana tasks based on Vendor.")
    parser.add_argument("--task-gid", help="Process a single task GID")
    parser.add_argument("--project-gid", help="Process all incomplete tasks in a project GID")
    parser.add_argument("--commit", action="store_true", help="Actually apply tags to Asana (default is dry-run)")
    args = parser.parse_args()

    if not args.task_gid and not args.project_gid:
        print("Error: Must provide either --task-gid or --project-gid")
        sys.exit(1)

    suppliers = get_supplier_list()
    if not suppliers:
        print("Warning: Could not fetch supplier list. Proceeding without dynamic supplier matching.")

    dry_run = not args.commit
    
    if dry_run:
        print("=== DRY RUN MODE: No changes will be made ===")
        
    if args.task_gid:
        process_task(args.task_gid, suppliers, dry_run)
    elif args.project_gid:
        process_project(args.project_gid, suppliers, dry_run)