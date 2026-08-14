import sys
import os
import json
import argparse
import requests

def get_token():
    token = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN") or os.environ.get("ASANA_PAT")
    if not token:
        # Fallback to config file
        for path in [
            os.path.join(os.path.dirname(__file__), "ASANAconfig.json"),
            r"C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\Asana Scripts\ASANAconfig.json",
        ]:
            try:
                with open(path) as f:
                    cfg = json.load(f)
                token = cfg.get("ASANA_PERSONAL_ACCESS_TOKEN") or cfg.get("ASANA_PAT")
                if token:
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                continue
    if not token:
        print(json.dumps({"error": "ASANA_PERSONAL_ACCESS_TOKEN not set"}), file=sys.stderr)
        sys.exit(1)
    return token

def headers():
    return {"Authorization": f"Bearer {get_token()}", "Accept": "application/json"}

def update_task_custom_field(task_gid, custom_field_gid, new_enum_value_gid, dry_run=True):
    url = f"https://app.asana.com/api/1.0/tasks/{task_gid}"
    payload = {
        "data": {
            "custom_fields": {
                custom_field_gid: new_enum_value_gid
            }
        }
    }
    
    if dry_run:
        print(f"[DRY RUN] Would update task {task_gid} with custom field {custom_field_gid} to {new_enum_value_gid}")
        return True
    
    try:
        r = requests.put(url, headers=headers(), json=payload)
        r.raise_for_status()
        print(f"Successfully updated task {task_gid}")
        return True
    except requests.HTTPError as e:
        print(f"Error updating task {task_gid}: {e.response.text}", file=sys.stderr)
        return False

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update Asana task custom field")
    parser.add_argument("--task-gids", nargs='+', help="List of task GIDs to update", required=True)
    parser.add_argument("--custom-field-gid", help="GID of the custom field to update", required=True)
    parser.add_argument("--new-enum-gid", help="GID of the new enum value to set", required=True)
    parser.add_argument("--commit", action="store_true", help="Actually apply the update")
    args = parser.parse_args()

    success_count = 0
    fail_count = 0
    for gid in args.task_gids:
        if update_task_custom_field(gid, args.custom_field_gid, args.new_enum_gid, dry_run=not args.commit):
            success_count += 1
        else:
            fail_count += 1
    
    if args.commit:
        print(f"\nUpdate complete. Success: {success_count}, Failed: {fail_count}")
    else:
        print(f"\nDry run complete. {success_count} tasks would be updated.")