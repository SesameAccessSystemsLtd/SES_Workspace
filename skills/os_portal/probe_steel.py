#!/usr/bin/env python3
"""Probe Steel Profile BOM tasks: their subtasks (vendor order tasks), completeness,
and how they relate to Karol sub-BOMs (by job / by project name)."""
import os, re, requests
from pathlib import Path

def tok():
    for k in ("ASANA_PERSONAL_ACCESS_TOKEN","ASANA_PAT"):
        if os.environ.get(k): return os.environ[k].strip()
    for line in Path(r"C:/Users/GilesHugo/.hermes/.env").read_text().splitlines():
        line=line.strip()
        for k in ("ASANA_PERSONAL_ACCESS_TOKEN","ASANA_PAT"):
            if line.startswith(f"{k}="): return line.split("=",1)[1].strip().strip('"').strip("'")
    return ""

BASE="https://app.asana.com/api/1.0"; H={"Authorization":f"Bearer {tok()}","Accept":"application/json"}
def get(p,params=None):
    r=requests.get(f"{BASE}{p}",headers=H,params=params,timeout=30)
    try: r.raise_for_status()
    except Exception as e: return {"error":str(e)}
    return r.json()
def pag(p,params=None,pages=20):
    params=dict(params or {}); params["limit"]=100; out=[]; off=None
    for _ in range(pages):
        if off: params["offset"]=off
        j=get(p,params)
        if "error" in j or "data" not in j: return out
        out.extend(j.get("data",[]))
        np=j.get("next_page")
        if np and np.get("offset"): off=np["offset"]
        else: break
    return out

STEEL_GIDS = {
    "1588 America 1":"1216623324565758",
    "1522 CHANEL 43CG":"1208407612216484",
    "1591 Cambridge":"1211534360702585",  # heuristic, may be wrong
}
# fetch all steel profile BOM tasks from Build/Project Orders + Purchasing Dept
print("=== fetch Steel Profile BOM tasks from Build/Project Orders ===")
BO="848657415032331"
tasks=pag(f"/projects/{BO}/tasks",{"opt_fields":"name,gid,completed,notes","limit":50})
steel=[t for t in tasks if "steel profile bom" in (t.get("name") or "").lower()]
print(f"  {len(steel)} steel tasks found")
for t in steel[:20]:
    nm=t.get("name") or ""
    subs=pag(f"/tasks/{t['gid']}/subtasks",{"opt_fields":"name,gid,completed","limit":100})
    os_=sum(1 for s in subs if not s.get("completed"))
    done=sum(1 for s in subs if s.get("completed"))
    print(f"  [{'x' if t.get('completed') else ' '}] {nm[:54]:56} subs={len(subs):3} open={os_:3} done={done:3}")
    if len(subs)<=6 and subs:
        for s in subs[:6]:
            print(f"         - [{'x' if s.get('completed') else ' '}] {s.get('name','')[:50]}")

print("\n=== Steel Profile task subtasks detail for a few ===")
for gid in ["1216623324565758","1208407612216484"]:
    j=get(f"/tasks/{gid}",{"opt_fields":"name,completed,notes"})
    t=j.get("data",{})
    print(f"\n[{gid}] {t.get('name')} completed={t.get('completed')}")
    print(f"  notes: {(t.get('notes') or '')[:150].replace(chr(10),' | ')}")
    subs=pag(f"/tasks/{gid}/subtasks",{"opt_fields":"name,gid,completed,vendor.name","limit":100})
    print(f"  subtasks ({len(subs)}):")
    for s in subs[:15]:
        print(f"    [{'x' if s.get('completed') else ' '}] {s.get('name','')[:52]}")
