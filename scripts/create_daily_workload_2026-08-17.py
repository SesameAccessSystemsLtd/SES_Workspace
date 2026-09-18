import os, requests

home = os.path.expanduser("~")
token = None
for env_path in [os.path.join(home, ".hermes", ".env"), os.path.join(home, ".openclaw", ".env")]:
    try:
        with open(env_path) as f:
            for line in f:
                if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN"):
                    token = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        if token:
            break
    except FileNotFoundError:
        continue

assert token, "No PAT found"

h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

notes = """BOMs & Orders
- Agenda Bender meeting with Naomi - done
- High volume of BOMs pulled into the system; 5 new ALL BOMS roots today (1591, 1365, 1575, 1595, 1037)
- GAP automation chase + new order for outstanding BOMs and service tasks

Procurement / Payments
- Sorted invoices to Fatima
- CPS paid for MOT
- Addressed the clutch purchase
- CV Library 2-week recruitment access paid (order 21237)

Production / Drawings
- Sorted corridor and planning items
- Production updates + quotes for people
- 4 production drawings checked, updated, and distributed into the workshop
- Fixed machining tasks to Andy that were not correctly issued into production
- Laser cut orders tracked

Hydraulics
- Machine shop hydraulics chasing
- 2 off 1364.1005.C Actuator - Ram stroke 525mm (QC)

Stock / Sales / Recruitment
- conXion DIN and CON 35 linear actuator stock
- RFQ/ORDER - 5 off Concens CON35 linear actuator
- CRM adds: Angel Cagasan (Ardmore), Aryaman Garg (Junior Designer, BIG)
- New College Oxford - Sesame Lift (Sales)

Investigation
- Research on all recent BOMs pulled into Project Orders
- Reversing-lift kitting gap traced: imported parts in parts_control with 0 build pack GIDs assigned
"""

project_gid = "1147424298774210"
section_gid = "1216355623191051"

payload = {
    "data": {
        "name": "2026-08-17 Daily Workload - Heavy Day",
        "notes": notes,
        "projects": [project_gid],
        "custom_fields": {
            "1216356798129221": "1216356798129223",  # Priority: High
            "1216356799734180": "1216356799734184",  # Status: Done
            "1216357254285781": "1216357254285785",  # Effort: XL
            "1216356954621557": "1216356954621559",  # Area: Data Health
        },
        "memberships": [{"project": project_gid, "section": section_gid}],
    }
}

r = requests.post("https://app.asana.com/api/1.0/tasks", headers=h, json=payload)
print("HTTP", r.status_code)
d = r.json()
if r.status_code in (200, 201):
    print("TASK_GID=", d["data"]["gid"])
    print("NAME=", d["data"]["name"])
else:
    print("ERROR:", d)
