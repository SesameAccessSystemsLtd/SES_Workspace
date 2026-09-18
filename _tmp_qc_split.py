import sys, json
from pathlib import Path
sys.path.insert(0, str(Path(r"C:\Users\GilesHugo\SES_Workspace\skills\sql_db\scripts")))
import sync_utils as su

PROJECT_GID = "902269769783650"

url = f"{su.BASE}/projects/{PROJECT_GID}/tasks"
params = {"opt_fields": "gid,name,completed", "limit": 100}
all_tasks = []
page = 0
while url and page < 130:
    r = su.request_with_retry("GET", url, headers=su._headers(), params=params, timeout=30)
    body = r.json()
    all_tasks.extend(body.get("data", []))
    nxt = body.get("next_page")
    url = nxt.get("uri") if nxt else None
    params = {}
    page += 1

comp = [t for t in all_tasks if t.get("completed")]
incomp = [t for t in all_tasks if not t.get("completed")]
print(json.dumps({
    "pages": page,
    "total": len(all_tasks),
    "completed": len(comp),
    "incomplete": len(incomp),
    "incomplete_sample": [{"gid": t["gid"], "name": t.get("name", "")[:70]} for t in incomp[:10]],
}, indent=2, ensure_ascii=False))

out = Path(r"C:\Users\GilesHugo\SES_Workspace\_tmp_qc_split.json")
out.write_text(json.dumps({
    "completed": [t["gid"] for t in comp],
    "incomplete": [t["gid"] for t in incomp],
    "incomplete_detail": incomp,
}, ensure_ascii=False), encoding="utf-8")
print("saved:", out)
