"""
asana_query.py — Asana REST API query tool for OpenClaw agents.

Usage:
  python asana_query.py get_project <project_gid>
  python asana_query.py list_tasks <project_gid> [--incomplete-only] [--overdue-only] [--compact] [--include-custom-fields]
  python asana_query.py get_task <task_gid>
  python asana_query.py search_tasks <project_gid> <keyword>
  python asana_query.py task_counts <project_gid>

Output: JSON to stdout. Exit 0 on success, non-zero on error.
Auth: reads ASANA_PERSONAL_ACCESS_TOKEN from environment (set in ~/.hermes/.env).
"""

import sys
import os
import json
import argparse
import requests
from datetime import date

BASE = "https://app.asana.com/api/1.0"

def get_token():
    token = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN") or os.environ.get("ASANA_PAT")
    if not token:
        # Fallback: read from ASANAconfig.json next to this script or in scripts folder
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

def get(path, params=None):
    r = requests.get(f"{BASE}{path}", headers=headers(), params=params or {})
    r.raise_for_status()
    return r.json().get("data", r.json())

def cmd_get_project(gid):
    fields = "name,gid,status,created_at,due_date,current_status_update,members"
    data = get(f"/projects/{gid}", {"opt_fields": fields})
    print(json.dumps(data, indent=2))

def cmd_task_counts(gid):
    data = get(f"/projects/{gid}/task_counts", {
        "opt_fields": "num_tasks,num_incomplete_tasks,num_completed_tasks,num_milestones"
    })
    print(json.dumps(data, indent=2))

def cmd_list_tasks(gid, incomplete_only=False, overdue_only=False, compact=False, include_custom_fields=False):
    # compact mode omits notes to keep output small (use for heartbeats/scans)
    fields = "name,gid,completed,due_on,assignee.name,tags.name,memberships.section.name,parent.gid,dependencies.gid"
    if not compact:
        fields += ",notes"
    if include_custom_fields:
        fields += ",custom_fields" # Add custom_fields if the flag is set
    params = {
        "project": gid,
        "opt_fields": fields,
        "limit": 100,
    }
    if incomplete_only:
        params["completed_since"] = "now"  # returns only incomplete tasks

    tasks = []
    offset = None
    while True:
        if offset:
            params["offset"] = offset
        r = requests.get(f"{BASE}/tasks", headers=headers(), params=params)
        r.raise_for_status()
        body = r.json()
        tasks.extend(body.get("data", []))
        next_page = body.get("next_page")
        if not next_page:
            break
        offset = next_page.get("offset")

    today = date.today().isoformat()
    if overdue_only:
        tasks = [t for t in tasks if not t.get("completed") and t.get("due_on") and t["due_on"] < today]

    print(json.dumps(tasks, indent=2))

def cmd_get_task(gid):
    fields = "name,gid,completed,due_on,assignee.name,notes,projects.name,tags.name,stories,custom_fields,parent.gid,dependencies.gid" # Added custom_fields, parent.gid, dependencies.gid here too
    data = get(f"/tasks/{gid}", {"opt_fields": fields})
    print(json.dumps(data, indent=2))

def cmd_search_tasks(project_gid, keyword):
    # Use typeahead search within the project
    params = {
        "resource_type": "task",
        "query": keyword,
        "opt_fields": "name,gid,completed,due_on,assignee.name",
    }
    # Get workspace GID first
    project = get(f"/projects/{project_gid}", {"opt_fields": "workspace.gid"})
    workspace_gid = project.get("workspace", {}).get("gid")
    if not workspace_gid:
        print(json.dumps({"error": "Could not resolve workspace GID"}), file=sys.stderr)
        sys.exit(1)
    data = get(f"/workspaces/{workspace_gid}/typeahead", params)
    print(json.dumps(data, indent=2))

def main():
    parser = argparse.ArgumentParser(description="Asana query tool")
    parser.add_argument("command", choices=["get_project", "list_tasks", "get_task", "search_tasks", "task_counts"])
    parser.add_argument("gid", help="Project or task GID")
    parser.add_argument("keyword", nargs="?", help="Search keyword (search_tasks only)")
    parser.add_argument("--incomplete-only", action="store_true")
    parser.add_argument("--overdue-only", action="store_true")
    parser.add_argument("--compact", action="store_true", help="Omit notes field — use for heartbeats/scans to reduce output size")
    parser.add_argument("--include-custom-fields", action="store_true", help="Include custom fields in task list output (list_tasks only)")
    args = parser.parse_args()

    try:
        if args.command == "get_project":
            cmd_get_project(args.gid)
        elif args.command == "task_counts":
            cmd_task_counts(args.gid)
        elif args.command == "list_tasks":
            cmd_list_tasks(args.gid, incomplete_only=args.incomplete_only,
                           overdue_only=args.overdue_only, compact=args.compact,
                           include_custom_fields=args.include_custom_fields)
        elif args.command == "get_task":
            cmd_get_task(args.gid)
        elif args.command == "search_tasks":
            if not args.keyword:
                print(json.dumps({"error": "keyword required for search_tasks"}), file=sys.stderr)
                sys.exit(1)
            cmd_search_tasks(args.gid, args.keyword)
    except requests.HTTPError as e:
        print(json.dumps({"error": str(e), "status": e.response.status_code}), file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
