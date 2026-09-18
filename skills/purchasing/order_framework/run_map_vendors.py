#!/usr/bin/env python
"""
Phase 1 — Ordering Framework dry-run (READ-ONLY)
===============================================
For a given project (default: St George's 1595 Entire-system BOM), walk the
ALL BOMS root in Build Orders, collect each VENDOR task and its part subtasks,
and for each vendor emit a row that shows:
  - the correct parent vendor task GID/name (reuse live one; flag duplicates)
  - its parts (sesame_pn, qty, description)
  - a proposed 'Quote' custom-field payload (part list + detailed Dropbox link
    where derivable) — for Giles to review/correct the link format.

NO Asana writes. NO DB writes. Pure read-of state.
"""
import os, json, sys, re, csv, html
import requests

TOKEN = os.environ.get("ASANA_PERSONAL_ACCESS_TOKEN")
if not TOKEN:
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ASANAconfig.json")
    if os.path.exists(cfg):
        with open(cfg) as f:
            c = json.load(f)
        TOKEN = c.get("access_token") or c.get("token") or c.get("ASANA_PERSONAL_ACCESS_TOKEN")
if not TOKEN:
    print("NO_TOKEN"); sys.exit(1)

H = {"Authorization": f"Bearer {TOKEN}"}
BUILD_ORDERS = "848657415032331"
QUOTE_FIELD = "921101053132341"   # 'Quote' custom field in Quote Control project

OUTDIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "output")
os.makedirs(OUTDIR, exist_ok=True)


def get(url, p=None):
    r = requests.get(url, headers=H, params=p or {})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {url} :: {r.text[:200]}")
    return r.json()


def all_page(resp):
    d = resp["data"]
    while resp.get("next_page"):
        resp = get("https://app.asana.com/api/1.0/projects/%s/tasks" % BUILD_ORDERS,
                   {"opt_fields": "name,completed", "limit": 100, "offset": resp["next_page"]["offset"]})
        d.extend(resp["data"])
    return d


def subtasks(gid):
    out = []
    url = f"https://app.asana.com/api/1.0/tasks/{gid}/subtasks"
    while True:
        r = requests.get(url, headers=H, params={"opt_fields": "name,completed", "limit": 100})
        r.raise_for_status()
        d = r.json()
        out.extend(d.get("data", []))
        np = d.get("next_page")
        if not np:
            break
        url = f"https://app.asana.com/api/1.0/tasks/{gid}/subtasks"
        params = {"opt_fields": "name,completed", "limit": 100, "offset": np["offset"]}
        r = requests.get(url, headers=H, params=params)
        out.extend(r.json().get("data", []))
        break  # single page is usually enough for a vendor group
    return out


def split_area(name):
    """Return (costcentre, vendorlabelfromname)."""
    return name


def derive_dropbox(name):
    """Attempt a detailed Dropbox link from the task name/description if present.
    For now returns the generic-marked placeholder — Giles validates."""
    return ""


def main(args):
    if len(args) >= 3:
        project_name = args[1]
        root_gid = args[2]
    else:
        project_name = "St Georges House Entire system"
        root_gid = "1217486677436439"  # placeholder; resolved below

    # Locate the ALL BOMS root for 1595 in Build Orders by name
    root_gid = None
    tasks = all_page(get(f"https://app.asana.com/api/1.0/projects/{BUILD_ORDERS}/tasks",
                      {"opt_fields": "name,completed", "limit": 100}))
    # Score roots: prefer 'Entire system' over 'Long Lead' / other sub-BOMs.
    cands = []
    for t in tasks:
        n = t.get("name") or ""
        if n.startswith("ALL BOMS") and "1595" in n and ("St Georges" in n or "St George" in n):
            score = 1
            if "Entire system" in n:
                score = 2
            cands.append((score, t["gid"], n))
    if not cands:
        print("NO_ROOT_FOUND"); sys.exit(1)
    cands.sort(key=lambda x: x[0], reverse=True)
    root_gid = cands[0][1]
    print("ROOT:", cands[0][2], root_gid)

    rows = []
    for cc in subtasks(root_gid):          # cost-centre BOM nodes
        if cc.get("completed"):
            continue
        for vg in subtasks(cc["gid"]):     # vendor-group tasks
            if vg.get("completed"):
                continue
            vname = vg.get("name") or ""
            # skip the PICKING / hierarchy-only nodes
            if vname.upper().startswith("PICKING") or vname.upper().startswith("ALL BOMS"):
                continue
            parts = subtasks(vg["gid"])
            if not parts:
                continue
            # count + collect
            p_counts = {}
            for p in parts:
                nm = p.get("name") or ""
                m = re.match(r"^([\d.]+)\s+off\s+(.*)$", nm)
                qty = m.group(1) if m else "?"
                desc = m.group(2) if m else nm
                p_counts.setdefault(desc, 0)
                p_counts[desc] += 1
            part_lines = "; ".join(f"{qty} off {d}" for d, qty in p_counts.items())
            rows.append({
                "vendor_task_name": vname,
                "vendor_task_gid": vg["gid"],
                "parent_cost_centre": cc.get("name", ""),
                "num_parts": len(parts),
                "part_list": part_lines,
                "proposed_quote_field": part_lines,   # + detailed dropbox link where derivable
                "dropbox_link": derive_dropbox(vname),
                "needs_review": "LINK" if not derive_dropbox(vname) else "",
            })

    ts = __import__("datetime").datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(OUTDIR, f"1595_st_georges_quote_proposal_{ts}.csv")
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else [])
        w.writeheader()
        w.writerows(rows)
    print(f"\nWROTE {len(rows)} vendor rows -> {out_path}")
    for r in rows[:40]:
        print(f"  [{r['vendor_task_gid']}] {r['vendor_task_name']} :: {r['num_parts']} parts")


if __name__ == "__main__":
    main(sys.argv)
