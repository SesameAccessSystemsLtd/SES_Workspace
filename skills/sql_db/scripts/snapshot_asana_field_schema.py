r"""snapshot_asana_field_schema.py — regenerate data/asana_custom_fields_schema.json from
live Asana. Read-only: GET requests only; nothing is written to Asana or ops.db.

The file is a reference for humans and agents looking up field / option GIDs. It went
stale once already: the 2026-06-30 copy predated a renamed ORDER Status option, new
options and disabled ones. So the output carries a _snapshot header saying when it was
taken, and every enum option carries its `enabled` flag. Before writing an option to
Asana, resolve it by label from the live field (execute_requests.py does) — never copy
a GID out of this file into a write.

Prints what changed since the previous file (fields added / removed / renamed, options
added / removed / renamed / disabled) before overwriting it.

Run:  ..\..\..\.venv\Scripts\python.exe snapshot_asana_field_schema.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from path_config import p as _p  # noqa: E402
from sync_utils import BASE, get_token, request_with_retry  # noqa: E402

OUT = Path(_p("data.root")) / "asana_custom_fields_schema.json"

# The five projects the 2026-06-30 snapshot covered, in the same order.
PROJECTS = [
    "1211660707080749",  # Parts Control Project
    "852956628490855",   # Build Schedule Project
    "1142754335688166",  # Drawing Control Project
    "414986125671548",   # Suppliers Project
    "902269769783650",   # 1.0 - QC Issues project
]
ENUM_TYPES = {"enum", "multi_enum"}
SETTING_FIELDS = ("custom_field.gid,custom_field.name,custom_field.resource_subtype,"
                  "custom_field.enum_options.gid,custom_field.enum_options.name,"
                  "custom_field.enum_options.enabled")


def _get(url: str, params: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {get_token()}", "Accept": "application/json"}
    return request_with_retry("GET", url, headers=headers, params=params, timeout=30).json()


def fetch_project(project_gid: str) -> dict:
    name = _get(f"{BASE}/projects/{project_gid}", {"opt_fields": "name"})["data"]["name"]
    fields = []
    url = f"{BASE}/projects/{project_gid}/custom_field_settings"
    params: dict | None = {"opt_fields": SETTING_FIELDS, "limit": 100}
    while url:
        body = _get(url, params)
        for setting in body.get("data", []):
            cf = setting["custom_field"]
            field = {"name": cf["name"], "gid": cf["gid"], "type": cf.get("resource_subtype")}
            if field["type"] in ENUM_TYPES:
                field["enum_options"] = [{"name": o["name"], "gid": o["gid"], "enabled": o["enabled"]}
                                         for o in cf.get("enum_options") or []]
            fields.append(field)
        nxt = body.get("next_page")
        url, params = (nxt["uri"], None) if nxt else (None, None)
    return {"project_name": name, "project_gid": project_gid, "custom_fields": fields}


def _index(doc) -> tuple[dict, dict]:
    """(field gid -> field, project gid -> (name, {field gids})). Accepts the old bare-list format."""
    projects = doc["projects"] if isinstance(doc, dict) else doc
    fields, members = {}, {}
    for proj in projects:
        members[proj["project_gid"]] = (proj["project_name"], {f["gid"] for f in proj["custom_fields"]})
        for f in proj["custom_fields"]:
            fields[f["gid"]] = f
    return fields, members


def changes(old_doc, new_doc) -> list[str]:
    old_fields, old_members = _index(old_doc)
    new_fields, new_members = _index(new_doc)
    out = []
    for pgid in old_members.keys() - new_members.keys():
        out.append(f"project dropped from snapshot: {old_members[pgid][0]!r} ({pgid})")
    for pgid, (pname, gids) in new_members.items():
        old_name, before = old_members.get(pgid, (pname, set()))
        if old_name != pname:
            out.append(f"project {pgid} renamed {old_name!r} -> {pname!r}")
        out += [f"{pname}: field added {new_fields[g]['name']!r} ({g})" for g in sorted(gids - before)]
        out += [f"{pname}: field removed {old_fields[g]['name']!r} ({g})" for g in sorted(before - gids)]
    # Options belong to the field, not the project — compare each field once.
    for gid, field in new_fields.items():
        old = old_fields.get(gid)
        if old is None:
            continue
        where = f"{field['name'].strip()} ({gid})"
        if old["name"] != field["name"]:
            out.append(f"field {gid} renamed {old['name']!r} -> {field['name']!r}")
        old_opts = {o["gid"]: o for o in old.get("enum_options") or []}
        for opt in field.get("enum_options") or []:
            prev = old_opts.pop(opt["gid"], None)
            if prev is None:
                out.append(f"{where}: option added {opt['name']!r} ({opt['gid']})"
                           + ("" if opt["enabled"] else " [disabled]"))
                continue
            if prev["name"] != opt["name"]:
                out.append(f"{where}: option {opt['gid']} renamed {prev['name']!r} -> {opt['name']!r}")
            if prev.get("enabled", True) != opt["enabled"]:
                state = "re-enabled" if opt["enabled"] else "now disabled"
                out.append(f"{where}: option {state} {opt['name']!r} ({opt['gid']})")
        out += [f"{where}: option removed {o['name']!r} ({o['gid']})" for o in old_opts.values()]
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Regenerate the Asana custom-field schema snapshot (GET only).")
    ap.add_argument("--dry-run", action="store_true", help="fetch and print changes; do not write the file")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    taken_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = {
        "_snapshot": {
            "taken_at": taken_at,
            "source": "Asana API, GET /projects/{gid} and /projects/{gid}/custom_field_settings (read-only)",
            "regenerate_with": "skills/sql_db/scripts/snapshot_asana_field_schema.py",
            "warning": ("Point-in-time copy - stale from the moment it is written. Asana options get "
                        "renamed, added and disabled (enabled=false). Before writing an option to Asana, "
                        "resolve it by label from the live field, as execute_requests.py does; never copy "
                        "a GID from this file into a write."),
        },
        "projects": [fetch_project(gid) for gid in PROJECTS],
    }

    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else None
    if old is not None:
        prev = old.get("_snapshot", {}).get("taken_at") if isinstance(old, dict) else "undated list format"
        diff = changes(old, new)
        print(f"{len(diff)} change(s) since previous snapshot ({prev}):")
        for line in diff:
            print("  " + line)

    if args.dry_run:
        print("dry run - file not written")
        return
    tmp = OUT.with_name(OUT.name + ".tmp")
    tmp.write_text(json.dumps(new, indent=4) + "\n", encoding="utf-8")
    os.replace(tmp, OUT)
    print(f"wrote {OUT} (taken_at {taken_at})")


if __name__ == "__main__":
    main()
