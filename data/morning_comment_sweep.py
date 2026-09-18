import os, time, datetime, requests, json, sys

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

PROJ = {
    "Order Numbers": "854650811917168",
    "Picking/Order control": "848657415032333",
    "Goods in / Order Requests": "358790955858777",
    "Project Orders (Build Orders)": "848657415032331",
    "1.4 Purchasing Dept": "850066254549317",
    "Build Schedule": "852956628490855",
    "QC issues / Parts Needed": "737971594126804",
    "Design Feedback / Drawing Issues": "549468236923679",
}

now = datetime.datetime.now(datetime.timezone.utc)
since = (now - datetime.timedelta(hours=44)).strftime("%Y-%m-%dT%H:%M:%S.000Z")

def paged(url, params):
    out = []
    while True:
        r = requests.get(url, headers=h, params=params, timeout=30)
        if r.status_code != 200:
            return out, r.status_code
        j = r.json()
        out += (j.get("data") or [])
        off = (j.get("next_page") or {}).get("offset")
        if not off:
            return out, 200
        params = dict(params); params["offset"] = off

results = {}
for name, gid in PROJ.items():
    tasks, sc = paged(f"https://app.asana.com/api/1.0/projects/{gid}/tasks",
                      {"completed": "false", "modified_after": since, "limit": 100,
                       "opt_fields": "name,modified_at,permalink_url,assignee.name"})
    if sc != 200:
        results[name] = ("HTTP %s" % sc, [])
        continue
    cc = []
    for t in tasks:
        tg = t.get("gid")
        if not tg:
            continue
        stories, sc2 = paged(f"https://app.asana.com/api/1.0/tasks/{tg}/stories",
                             {"opt_fields": "created_at,created_by.name,type,text"})
        for s in stories or []:
            if s.get("type") != "comment":
                continue
            ca = s.get("created_at") or ""
            if ca >= since:
                cc.append((ca, t.get("name", "") or "", t.get("permalink_url", "") or "",
                           (s.get("created_by") or {}).get("name", "") or "?",
                           (s.get("text") or "").strip()))
        time.sleep(0.05)
    results[name] = (len(tasks), cc)

out = []
out.append("Now(UTC): " + now.strftime("%Y-%m-%dT%H:%M") + " | since " + since)
total = sum(len(v[1]) for v in results.values())
out.append(f"TOTAL new comments: {total}")
for name, (n, cc) in results.items():
    if isinstance(n, str):
        out.append(f"{name}: {n}")
        continue
    out.append(f"\n### {name}: {n} touched, {len(cc)} new comments")
    for ca, tn, url, who, txt in sorted(cc):
        out.append(f"  [{ca[:16]}] {who} | {tn[:70]}")
        out.append(f"      {txt[:400]}")
        out.append(f"      {url}")

open(r"C:\Users\GilesHugo\SES_Workspace\data\morning_comment_sweep.txt", "w", encoding="utf-8").write("\n".join(out))
print("\n".join(out))
