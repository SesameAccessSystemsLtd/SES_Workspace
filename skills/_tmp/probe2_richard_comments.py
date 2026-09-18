#!/usr/bin/env python
"""Probe 2: for all Richard-assigned tagged tasks, pull stories, find human-Giles
comments created inside last 60d. Print them for inspection (read-only)."""
import os, requests, datetime

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
HUMAN_GILES = "1142108324926610"
SVC = "1210751448247658"
TAGS = ["1163007497828518", "1164351154905410"]
WINDOW = datetime.date.today() - datetime.timedelta(days=60)
WSTART = WINDOW.isoformat()


def paginate(path, params):
    out, p = [], dict(params)
    url = path
    while True:
        full = url if url.startswith("http") else BASE + url
        r = requests.get(full, headers=H, params=p, timeout=25)
        if r.status_code != 200:
            print("ERR", full.split("?")[0], r.status_code, r.text[:150]); return out
        d = r.json()
        out.extend(d.get("data", []) or [])
        np_ = d.get("next_page") or {}
        if np_.get("offset"):
            url = BASE + np_["path"]; p = {}
        else:
            break
    return out


def main():
    tasks = {}
    for tag in TAGS:
        for it in paginate("/tags/%s/tasks" % tag, {"limit": 100, "opt_fields": "name,modified_at,completed,assignee.gid"}):
            a = (it.get("assignee") or {}).get("gid")
            if a and str(a) == RICHARD:
                g = it.get("gid")
                if g not in tasks:
                    tasks[g] = it
                    tasks[g]["tags"] = []
                tasks[g]["tags"].append(tag)
    print("Richard-assigned tasks:", len(tasks))
    # pre-filter by modified_at >= window start (a comment post marks task modified)
    mod_tasks = [g for g, t in tasks.items()
                 if (t.get("modified_at") or "")[:10] >= WSTART]
    print("  ...of which modified_at >= 60d-start:", len(mod_tasks))
    found = []
    for i, g in enumerate(mod_tasks):
        st = paginate("/tasks/%s/stories" % g, {"opt_fields": "created_at,created_by.gid,created_by.name,type,text", "limit": 100})
        for s in st:
            if s.get("type") != "comment":
                continue
            cb = s.get("created_by") or {}
            cb_gid = str(cb.get("gid") or "")
            if cb_gid not in (HUMAN_GILES, SVC):
                continue
            txt = (s.get("text") or "").strip()
            created = (s.get("created_at") or "")
            if not txt or not created:
                continue
            if created[:10] < WSTART:
                continue
            # exclude service-account HTML <body> Purchasing update boilerplate
            if cb_gid == SVC:
                low = txt.lower()
                if "<body>" in low or "purchasing update" in low:
                    continue
            found.append({"task": g, "name": (tasks[g].get("name") or "")[:60],
                          "date": created[:10], "author": cb.get("name"),
                          "text": txt[:400]})
        if (i + 1) % 60 == 0:
            print("  scanned", i + 1, "tasks; in-window Giles comments so far:", len(found))
    print("\nTOTAL in-window human-Giles (or non-boilerplate) comments:", len(found))
    for f in found:
        print("\n#", f["date"], "task", f["task"], "|", f["name"], "| author", f["author"])
        print("   ", f["text"].replace("\n", " ")[:300])


if __name__ == "__main__":
    main()
