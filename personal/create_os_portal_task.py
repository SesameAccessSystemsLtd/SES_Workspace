"""Create the AI Dev Work task: deploy the SES OS Portal to the VPS.
Giles requested it 2026-09-11 for Monday. Dev/process task -> Giles Ai Dev Work VPS/Webhooks section."""
import os, json, requests
from datetime import datetime, timezone

home = os.path.expanduser("~")
tok = ""
for ep in [os.path.join(home, ".hermes", ".env"), os.path.join(home, ".openclaw", ".env")]:
    try:
        for line in open(ep):
            if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN"):
                tok = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
        if tok:
            break
    except FileNotFoundError:
        continue

H = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}
PROJ = "1147424298774210"          # Giles Ai Dev Work
SECTION = "1216357087488035"       # 🔌 VPS / Webhooks
GILES = "1142108324926610"

name = "Deploy SES OS Portal to VPS (Traefik route + Cloudflare Access) — portal.yml staged"
notes = (
    "Goal: host the SES OS Portal (static dashboards from ops.db) as a live portal for the business.\n\n"
    "VERIFIED ARCHITECTURE (2026-09-11 recon, nothing changed):\n"
    "- VPS front door = Traefik; add a site by dropping a .yml in /docker/traefik/dynamic/ (hot-reload).\n"
    "- hooks.sesameaccess.com already routes via Traefik -> Asana webhook receiver v2.\n"
    "- Proposed: os.sesameaccess.com -> static server -> /opt/ses/portal (pushed HTML).\n\n"
    "DECISION (Giles): run portal generation LOCALLY, PUSH generated HTML to the host. Do NOT cron the\n"
    "refresh on the VPS (local ops.db is the single source of truth). VPS = dumb target.\n\n"
    "MONDAY TASKS:\n"
    "1. Confirm hostname (os./portal./ops.sesameaccess.com) + push cadence.\n"
    "2. Stage + apply portal.yml (copy of hooks.yml) + static server (nginx/caddy container, host-net, loopback).\n"
    "3. Cloudflare: DNS orange-cloud + Access policies (IP-allowlist office + email-OTP Giles for remote).\n"
    "4. Write the local push script (rsync/scp over existing key) + run it after refresh jobs; make idempotent.\n"
    "5. Verify: public 200 + Access challenge; push idempotent; local data change appears after one push.\n\n"
    "ALSO (same session): implement the two new guardrails -\n"
    "D1 external-write identification (PATH_CONFIG `external:` block + loud EXTERNAL banner on any non-local\n"
    "   path write; default assumption = LOCAL).\n"
    "D2 SES_Brain write guardrail (writes only to raw/ + projects/*; audit recent writes).\n\n"
    "Docs: plans/os-portal-hosting-plan-2026-09-11.md (recipe) · "
    "plans/os-portal-and-guardrails-outline-2026-09-11.md (approach) · "
    "reports/os-portal-hosting-assessment-2026-09-11.md (assessment)."
)

payload = {
    "data": {
        "name": name,
        "notes": notes,
        "assignee": GILES,
        "projects": [PROJ],
        "custom_fields": {
            "1216356798129221": "1216356798129223",   # Priority = High
            "1216356799734180": "1216356799734181",   # Status = Not Started
            "1216357254285781": "1216357254285783",   # Effort = M
            "1216356954621557": "1216356954621561",   # Area = VPS / Webhooks
        },
        "memberships": [{"project": PROJ, "section": SECTION}],
    }
}
r = requests.post("https://app.asana.com/api/1.0/tasks", headers=H, json=payload)
if r.status_code in (200, 201):
    d = r.json()["data"]
    print("CREATED", d["gid"], "|", d["name"])
    log = {"timestamp": datetime.now(timezone.utc).isoformat(), "action": "create_task",
           "task_gid": d["gid"], "project_gid": PROJ, "section_gid": SECTION, "outcome": "success",
           "note": "AI Dev Work task: Deploy SES OS Portal to VPS (Monday)"}
    with open(os.path.join(home, "SES_Workspace", "data", "asana_write_log.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(log) + "\n")
    print("logged")
else:
    print("FAILED", r.status_code, r.text[:400])
