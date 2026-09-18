"""READ-ONLY Asana usage & dependency audit (2026-09-14). v2 — no task_counts (returns {}).
Uses GET /projects/{gid}/tasks with capped pagination + early exit.
No writes anywhere.
Outputs: data/audit/asana_projects_audit.csv, data/audit/asana_audit_summary.txt
"""
import os, csv, time, collections, datetime
import requests

WS = "357939803556123"
OUT_DIR = r"C:\Users\GilesHugo\SES_Workspace\data\audit"
os.makedirs(OUT_DIR, exist_ok=True)

home = os.path.expanduser("~"); tok = None
for ep in [os.path.join(home, ".hermes", ".env"), os.path.join(home, ".openclaw", ".env")]:
    try:
        for line in open(ep):
            if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN"):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
    except FileNotFoundError:
        pass
    if tok:
        break
h = {"Authorization": f"Bearer {tok}"}
BASE = "https://app.asana.com/api/1.0"

def get(path, params, tries=4):
    for a in range(tries):
        r = requests.get(BASE + path, headers=h, params=params, timeout=40)
        if r.status_code == 429:
            time.sleep(2 + a * 2); continue
        return r
    return r

# 1) all projects
projs = []; off = None
while True:
    p = {"opt_fields": "name,archived,created_at,modified_at,owner.name", "limit": 100}
    if off: p["offset"] = off
    r = get(f"/workspaces/{WS}/projects", p)
    if r.status_code != 200:
        print("project list HTTP", r.status_code); break
    j = r.json(); projs += (j.get("data") or [])
    off = (j.get("next_page") or {}).get("offset")
    if not off: break
print(f"projects fetched: {len(projs)}", flush=True)

rows = []
act_projs = [p for p in projs if not p.get("archived")]
for i, p in enumerate(act_projs):
    gid = p["gid"]
    # capped task scan: up to 2 pages (200 tasks), enough to detect open work + recent activity
    total = 0; any_inc = False; maxmod = ""; pages = 0; cur_off = None
    while pages < 2:
        params = {"opt_fields": "completed,modified_at", "limit": 100}
        if cur_off: params["offset"] = cur_off
        r = get(f"/projects/{gid}/tasks", params)
        if r.status_code != 200: break
        j = r.json(); data = j.get("data") or []
        total += len(data)
        for t in data:
            if not t.get("completed"): any_inc = True
            m = (t.get("modified_at") or "")[:19]
            if m > maxmod: maxmod = m
        cur_off = (j.get("next_page") or {}).get("offset")
        pages += 1
        if not cur_off: break
        # early exit: if page full and we already have incomplete + recent data, one more page is enough signal
    rows.append({
        "gid": gid, "name": (p.get("name") or "").strip(),
        "archived": p.get("archived"),
        "created": (p.get("created_at") or "")[:10],
        "modified": (p.get("modified_at") or "")[:10],
        "owner": (p.get("owner") or {}).get("name") if p.get("owner") else "",
        "task_sample": total, "any_incomplete": any_inc, "last_task_activity": maxmod,
    })
    if i % 50 == 0:
        print(f"  scanned {i}/{len(projs)}", flush=True)
    time.sleep(0.04)

csv_path = os.path.join(OUT_DIR, "asana_projects_audit.csv")
with open(csv_path, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
print("WROTE", csv_path, flush=True)

# classification (active only)
act = rows
arch = [p for p in projs if p.get("archived")]
import re
jobre = re.compile(r"^\s*\d{3,4}\b")
testre = re.compile(r"(test|testing|tester|zapier|flowsana|beta|demo|sandbox|dummy|trial)", re.I)
def cls(r):
    n = r["name"]
    if r["archived"]: return "ARCHIVED"
    if testre.search(n): return "TEST/CRUFT"
    if jobre.match(n):
        if r["task_sample"] == 0: return "JOB-BOARD empty"
        if not r["any_incomplete"]: return "JOB-BOARD done/dormant"
        return "JOB-BOARD has-open"
    if r["task_sample"] == 0: return "CONTROL empty"
    if not r["any_incomplete"]: return "CONTROL no-open"
    return "CONTROL/NAMED has-open"
roll = collections.Counter()
for r in act:
    r["cls"] = cls(r); roll[r["cls"]] += 1

S = []
S.append(f"Asana usage audit v2 — {datetime.datetime.now().isoformat(timespec='seconds')}")
S.append(f"Total {len(rows)} | active {len(act)} | archived {len(arch)}")
S.append("")
S.append("ACTIVE classification:")
for k, v in sorted(roll.items(), key=lambda x: -x[1]):
    S.append(f"  {k:28} {v}")
inc = [r for r in act if r["any_incomplete"]]
S.append("")
S.append(f"Active projects WITH open (incomplete) work: {len(inc)}")
S.append(f"Active projects sampled with ZERO tasks: {len([r for r in act if r['task_sample']==0])}")
S.append("")
S.append("Top 30 active by sample size:")
for r in sorted(act, key=lambda x: -x["task_sample"])[:30]:
    S.append(f"  {r['task_sample']:5} tasks | inc={r['any_incomplete']} | act {r['last_task_activity'][:10] or '-'} | {r['name'][:52]}")
S.append("")
S.append("Active ZERO-task projects (archive candidates):")
for r in [x for x in act if x["task_sample"] == 0][:60]:
    S.append(f"  {r['name'][:58]:58} | cre {r['created']} mod {r['modified']}")
txt = "\n".join(S)
open(os.path.join(OUT_DIR, "asana_audit_summary.txt"), "w", encoding="utf-8").write(txt)
print("\n" + txt)
