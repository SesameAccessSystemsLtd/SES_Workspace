"""
asana_batch_updater.py — Reusable Asana batch custom-field updater.

Uses the Asana Batch API to push up to 10 task updates per HTTP request,
significantly reducing API call volume for bulk operations.

As a module (import by other scripts):
    from asana_batch_updater import push_updates, get_asana_token
    ok, fail = push_updates(updates, token, dry_run=True)

As a standalone CLI:
    python asana_batch_updater.py --file updates.json           # dry run
    python asana_batch_updater.py --file updates.json --commit  # apply

Expected JSON input format:
{
  "updates": [
    {
      "task_gid": "1234567890",
      "task_name": "Panel Bracket x50 (label only)",
      "custom_field_gid": "1199111659373956",
      "new_enum_gid": "1200721961626388"
    }
  ]
}
"""

import os
import sys
import json
import time
import argparse
import requests

BASE = "https://app.asana.com/api/1.0"
BATCH_SIZE = 10
MAX_RETRIES = 10


def get_asana_token():
    token = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN") or os.environ.get("ASANA_PAT")
    if not token:
        search_paths = [
            os.path.join(os.path.dirname(__file__), "ASANAconfig.json"),
            r"C:\Users\GilesHugo\OneDrive - SESAME ACCESS AZURE\OneDrive Documents\LAB. SESAME\Asana Scripts\ASANAconfig.json",
        ]
        for path in search_paths:
            try:
                with open(path) as f:
                    cfg = json.load(f)
                token = cfg.get("ASANA_PERSONAL_ACCESS_TOKEN") or cfg.get("ASANA_PAT")
                if token:
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                continue
    return token


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def push_updates(updates, token, dry_run=True):
    """
    Push a list of custom field updates to Asana using the Batch API.

    Args:
        updates: list of dicts, each with:
            task_gid (str), task_name (str), custom_field_gid (str), new_enum_gid (str)
        token (str): Asana PAT
        dry_run (bool): if True, print what would happen but make no API calls

    Returns:
        (ok_count, fail_count)
    """
    if not updates:
        print("  No updates to push.")
        return 0, 0

    total_ok = 0
    total_fail = 0
    chunks = [updates[i : i + BATCH_SIZE] for i in range(0, len(updates), BATCH_SIZE)]

    for i, chunk in enumerate(chunks, 1):
        print(f"  Batch {i}/{len(chunks)} ({len(chunk)} task(s))...", flush=True)

        if dry_run:
            for u in chunk:
                print(f"    [DRY RUN] {u.get('task_name', u['task_gid'])} → {u['new_enum_gid']}")
            total_ok += len(chunk)
            continue

        actions = [
            {
                "method": "put",
                "relative_path": f"/tasks/{u['task_gid']}",
                "data": {
                    "custom_fields": {u["custom_field_gid"]: u["new_enum_gid"]}
                },
            }
            for u in chunk
        ]

        r = None
        for attempt in range(MAX_RETRIES):
            r = requests.post(
                f"{BASE}/batch",
                headers=_headers(token),
                json={"data": {"actions": actions}},
            )
            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After", 60))
                print(f"  [WARN] Rate limit hit — waiting {wait}s (attempt {attempt + 1}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            break

        if r.status_code not in (200, 201):
            print(f"  ERROR: Batch {i} HTTP {r.status_code}: {r.text[:300]}")
            total_fail += len(chunk)
            continue

        results = r.json().get("data", [])
        for j, res in enumerate(results):
            status = res.get("status_code", 0)
            if status in (200, 201):
                total_ok += 1
            else:
                total_fail += 1
                name = chunk[j].get("task_name", chunk[j]["task_gid"])
                print(f"  ERROR: {name} — status {status} — {res.get('body', {})}")

    return total_ok, total_fail


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Push Asana custom field updates from a JSON file via the Batch API."
    )
    parser.add_argument("--file", required=True, help="Path to updates JSON file")
    parser.add_argument("--commit", action="store_true", help="Apply updates (default: dry run)")
    args = parser.parse_args()

    token = get_asana_token()
    if not token:
        sys.exit("ERROR: ASANA_PERSONAL_ACCESS_TOKEN not found in environment or ASANAconfig.json")

    with open(args.file) as f:
        payload = json.load(f)

    updates = payload.get("updates", [])
    mode = "APPLYING" if args.commit else "DRY RUN"
    print(f"{mode} — {len(updates)} update(s) from {args.file}")

    ok, fail = push_updates(updates, token, dry_run=not args.commit)
    print(f"\nDone. Applied: {ok}  Failed: {fail}")
    sys.exit(1 if fail else 0)
