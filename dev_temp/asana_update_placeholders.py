#!/usr/bin/env python3
"""Scan workspace + project subtree for [PROJECT NAME] placeholder variants in task names
and replace them with '1591 - University of Cambridge'.
Case-insensitive matching. Robust: workspace search + full subtree crawl, combined & deduped.
"""
import os
import re
import sys
import time
import json
import httpx

# ---------------- Config ----------------
WORKSPACE_GID = "357939803556123"
PROJECT_GID = "1216293165459681"
REPLACEMENT = "1591 - University of Cambridge"
PLACEHOLDER_RE = re.compile(r"\[\s*project\s*name\s*\]", re.IGNORECASE)
BASE = "https://app.asana.com/api/1.0"
PAGE_SIZE = 100
RATE_LIMIT_SLEEP = 0.6

# ---------------- Auth ----------------
def read_pat():
    candidates = [
        os.path.expanduser("~/.Hermes/.env"),
        os.path.expanduser("~/.openclaw/.env"),
        os.path.expanduser("~/.openclaw.ARCHIVED/.env"),
    ]
    for path in candidates:
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val:
                            return val, path
        except FileNotFoundError:
            continue
    sys.exit("ERROR: ASANA_PERSONAL_ACCESS_TOKEN not found in any candidate .env file")

PAT, PAT_SRC = read_pat()
HEADERS = {"Authorization": f"Bearer {PAT}", "Accept": "application/json"}
client = httpx.Client(headers=HEADERS, timeout=60.0)

def api_get(url, params):
    for attempt in range(5):
        try:
            r = client.get(url, params=params)
        except httpx.HTTPError as e:
            print(f"  !! request error ({e}); retry {attempt+1}", file=sys.stderr)
            time.sleep(2)
            continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 * (attempt + 1))
            continue
        print(f"  GET error {r.status_code}: {r.text[:300]}", file=sys.stderr)
        sys.exit(f"FATAL: GET {url} -> {r.status_code}")
    sys.exit(f"FATAL: too many retries on GET {url}")

def api_post(url, data):
    for attempt in range(5):
        try:
            r = client.post(url, json=data)
        except httpx.HTTPError as e:
            print(f"  !! request error ({e}); retry {attempt+1}", file=sys.stderr)
            time.sleep(2)
            continue
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 * (attempt + 1))
            continue
        print(f"  POST error {r.status_code}: {r.text[:300]}", file=sys.stderr)
        sys.exit(f"FATAL: POST {url} -> {r.status_code}")
    sys.exit(f"FATAL: too many retries on POST {url}")

def api_put(url, data):
    for attempt in range(5):
        try:
            r = client.put(url, json=data)
        except httpx.HTTPError as e:
            print(f"  !! request error ({e}); retry {attempt+1}", file=sys.stderr)
            time.sleep(2)
            continue
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 * (attempt + 1))
            continue
        print(f"  PUT error {r.status_code}: {r.text[:300]}", file=sys.stderr)
        sys.exit(f"FATAL: PUT {url} -> {r.status_code}")
    sys.exit(f"FATAL: too many retries on PUT {url}")

# ---------------- Step 1: workspace search ----------------
def workspace_search(query):
    """Paginate through all results of workspace tasks search."""
    results = {}  # gid -> name
    offset = None
    while True:
        params = {"query": query, "opt_fields": "name", "limit": PAGE_SIZE}
        if offset:
            params["offset"] = offset
        data = api_get(f"{BASE}/workspaces/{WORKSPACE_GID}/tasks/search", params)
        for item in data.get("data", []):
            results[item["gid"]] = item.get("name", "")
        next_page = data.get("next_page") or {}
        offset = next_page.get("offset")
        if not offset:
            break
        time.sleep(RATE_LIMIT_SLEEP)
    return results

# ---------------- Step 2: project + subtree crawl ----------------
def get_project_tasks():
    """All tasks directly in project, paginated. Returns {gid: name}."""
    results = {}
    offset = None
    while True:
        params = {"opt_fields": "name", "limit": PAGE_SIZE}
        if offset:
            params["offset"] = offset
        data = api_get(f"{BASE}/projects/{PROJECT_GID}/tasks", params)
        for item in data.get("data", []):
            results[item["gid"]] = item.get("name", "")
        next_page = data.get("next_page") or {}
        offset = next_page.get("offset")
        if not offset:
            break
        time.sleep(RATE_LIMIT_SLEEP)
    return results

def get_subtasks(task_gid):
    """Return {gid: name} of all subtasks for a task, paginated."""
    results = {}
    offset = None
    while True:
        params = {"opt_fields": "name", "limit": PAGE_SIZE}
        if offset:
            params["offset"] = offset
        data = api_get(f"{BASE}/tasks/{task_gid}/subtasks", params)
        for item in data.get("data", []):
            results[item["gid"]] = item.get("name", "")
        next_page = data.get("next_page") or {}
        offset = next_page.get("offset")
        if not offset:
            break
        time.sleep(RATE_LIMIT_SLEEP)
    return results

def crawl_subtree(root_gid_to_name):
    """BFS through subtasks of every task. Returns {gid: name} covering full tree."""
    all_tasks = dict(root_gid_to_name)
    frontier = list(root_gid_to_name.keys())
    visited = set()
    while frontier:
        tgid = frontier.pop()
        if tgid in visited:
            continue
        visited.add(tgid)
        subs = get_subtasks(tgid)
        for sgid, sname in subs.items():
            if sgid not in all_tasks:
                all_tasks[sgid] = sname
                frontier.append(sgid)
        time.sleep(RATE_LIMIT_SLEEP)
    return all_tasks

# ---------------- Main ----------------
def main():
    print(f"Using PAT from: {PAT_SRC}")
    print(f"Workspace: {WORKSPACE_GID} | Project: {PROJECT_GID}")
    print(f"Replacement name: {REPLACEMENT!r}")
    print(f"Placeholder regex: {PLACEHOLDER_RE.pattern}")
    print()

    # Build combined task set from all three approaches, dedupe by gid.
    combined = {}  # gid -> name

    # Search 1: 'PROJECT NAME'
    print(">> Workspace search #1: 'PROJECT NAME'")
    s1 = workspace_search("PROJECT NAME")
    print(f"   -> {len(s1)} tasks")
    combined.update(s1)

    # Search 2: 'project name' (lowercase, catches any other variant casing)
    print(">> Workspace search #2: 'project name'")
    s2 = workspace_search("project name")
    print(f"   -> {len(s2)} tasks")
    combined.update(s2)

    # Search 3: 'Project Name'
    print(">> Workspace search #3: 'Project Name'")
    s3 = workspace_search("Project Name")
    print(f"   -> {len(s3)} tasks")
    combined.update(s3)

    # Full subtree crawl from project tasks
    print(f">> Project {PROJECT_GID} tasks + full subtree crawl")
    root_tasks = get_project_tasks()
    print(f"   project tasks: {len(root_tasks)}")
    tree_tasks = crawl_subtree(root_tasks)
    print(f"   subtree total: {len(tree_tasks)}")
    combined.update(tree_tasks)

    print(f"\n>> Combined unique tasks: {len(combined)}")

    # Filter to only those with a placeholder variant in name (case-insensitive)
    matches = {gid: name for gid, name in combined.items() if PLACEHOLDER_RE.search(name or "")}
    print(f">> Tasks containing placeholder (any case): {len(matches)}")
    for gid, name in sorted(matches.items(), key=lambda kv: kv[1].lower()):
        print(f"   - {gid}: {name!r}")

    if not matches:
        print("\nNo tasks need updating. Done.")
        return

    print(f"\n>> Updating tasks...")
    updated = []
    for gid, old_name in sorted(matches.items(), key=lambda kv: kv[1].lower()):
        # Replace every occurrence of the placeholder(s) case-insensitively
        new_name = PLACEHOLDER_RE.sub(REPLACEMENT, old_name)
        if new_name == old_name:
            print(f"   SKIP {gid} (no change): {old_name!r}")
            continue
        api_put(f"{BASE}/tasks/{gid}", {"data": {"name": new_name}})
        updated.append((gid, old_name, new_name))
        print(f"   UPDATED {gid}")
        print(f"      old: {old_name!r}")
        print(f"      new: {new_name!r}")
        time.sleep(RATE_LIMIT_SLEEP)

    print(f"\n>> Verified: {len(updated)} tasks updated.")

    # Save result manifest for reporting
    manifest_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asana_update_result.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"updated": [{"gid": g, "old": o, "new": n} for g, o, n in updated]},
                  f, indent=2, ensure_ascii=False)
    print(f"   Manifest written to {manifest_path}")

if __name__ == "__main__":
    main()
