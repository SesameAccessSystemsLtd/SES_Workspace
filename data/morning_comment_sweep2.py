import os, time, datetime, requests

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
WS = "357939803556123"

now = datetime.datetime.now(datetime.timezone.utc)
since = (now - datetime.timedelta(hours=40)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

# One cheap workspace search: recently-modified, open tasks (production-wide)
tasks = []
params = {"completed": "false", "modified_after": since, "limit": 100,
          "opt_fields": "name,modified_at,permalink_url,memberships.project.name,assignee.name"}
while True:
    r = requests.get(f"https://app.asana.com/api/1.0/workspaces/{WS}/tasks/search", headers=h, params=params, timeout=30)
    if r.status_code != 200:
        print("search HTTP", r.status_code, r.text[:200]); break
    j = r.json(); tasks += (j.get("data") or [])
    off = (j.get("next_page") or {}).get("offset")
    if not off or len(tasks) >= 300:
        break
    params = dict(params); params["offset"] = off

tasks.sort(key=lambda t: t.get("modified_at") or "", reverse=True)
print(f"modified in last 40h: {len(tasks)} (capped {len(tasks[:120])})")
print("=" * 70)

def projname(t):
    ms = t.get("memberships") or []
    return ", ".join(m["project"]["name"] for m in ms if m.get("project")) or "-"

found = []
for t in tasks[:120]:
    tg = t.get("gid")
    if not tg: continue
    r = requests.get(f"https://app.asana.com/api/1.0/tasks/{tg}/stories", headers=h,
                     params={"opt_fields": "created_at,created_by.name,type,text"}, timeout=30)
    if r.status_code != 200: continue
    for s in r.json().get("data", []):
        if s.get("type") != "comment": continue
        ca = s.get("created_at") or ""
        if ca >= since:
            found.append((ca, t.get("name", ""), t.get("permalink_url", ""), projname(t),
                          (s.get("created_by") or {}).get("name", "?"),
                          (s.get("text") or "").strip()))
    time.sleep(0.03)

found.sort()
print(f"NEW COMMENTS: {len(found)}\n")
for ca, tn, url, pj, who, txt in found:
    print(f"[{ca[:16]}] {who}  ({pj})")
    print(f"   {tn[:75]}")
    print(f"   {txt[:400]}")
    print(f"   {url}\n")
