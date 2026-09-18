#!/usr/bin/env python
"""Probe: count Richard-assigned tagged tasks + inspect qualifying human-Giles comments.
Read-only. Never prints the token."""
import os, requests, json, sys, datetime

home = os.path.expanduser("~")
tok = ""
for ep in [os.path.join(home, ".hermes", ".env"), os.path.join(home, ".openclaw", ".env")]:
    try:
        for line in open(ep, encoding="utf-8", errors="ignore"):
            if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN"):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    except FileNotFoundError:
        continue
    if tok:
        break

H = {"Authorization": "Bearer " + tok}
BASE = "https://app.asana.com/api/1.0"
RICHARD = "357614200440807"


def paginate(path, params):
    out, p = [], dict(params)
    url = path
    while True:
        full = url if url.startswith("http") else BASE + url
        r = requests.get(full, headers=H, params=p, timeout=25)
        if r.status_code != 200:
            print("ERR", full.split("?")[0], r.status_code, r.text[:180])
            return out
        d = r.json()
        out.extend(d.get("data", []) or [])
        np_ = d.get("next_page") or {}
        if np_.get("offset"):
            url = BASE + np_["path"]
            p = {}
        else:
            break
    return out


def main():
    window = datetime.date.today() - datetime.timedelta(days=60)
    all_tasks = {}
    for tag in ["1163007497828518", "1164351154905410"]:
        t = paginate("/tags/%s/tasks" % tag,
                     {"limit": 100, "opt_fields": "name,assignee.gid,completed,memberships.project.name"})
        for it in t:
            a = (it.get("assignee") or {}).get("gid")
            if a and str(a) == RICHARD:
                g = it.get("gid")
                if g not in all_tasks:
                    all_tasks[g] = {"name": it.get("name"), "tags": [], "done": it.get("completed"),
                                    "projects": [m.get("project", {}).get("name") for m in (it.get("memberships") or [])]}
                all_tasks[g]["tags"].append(tag)
    print("RICHARD-ASSIGNED tagged tasks (both tags, dedup):", len(all_tasks))


if __name__ == "__main__":
    main()
