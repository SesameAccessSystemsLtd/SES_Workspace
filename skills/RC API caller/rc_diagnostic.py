import os, base64, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / ".env")

client_id     = os.getenv("RC_CLIENT_ID")
client_secret = os.getenv("RC_CLIENT_SECRET")
jwt           = os.getenv("RC_JWT")
server        = os.getenv("RC_SERVER")

creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
resp = requests.post(
    f"{server}/restapi/oauth/token",
    headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
    data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt},
    timeout=30,
)
resp.raise_for_status()
token = resp.json()["access_token"]
print("Auth OK")

# All voice calls today — no recording filter
r = requests.get(
    f"{server}/restapi/v1.0/account/~/call-log",
    headers={"Authorization": f"Bearer {token}"},
    params={"type": "Voice", "dateFrom": "2026-06-25T00:00:00Z", "perPage": 100},
    timeout=30,
)
r.raise_for_status()
records = r.json().get("records", [])
print(f"Total calls today (account-wide, no recording filter): {len(records)}")

for c in records[:20]:
    rec = c.get("recording") or {}
    frm = c.get("from") or {}
    name = frm.get("name", "?")
    ext  = frm.get("extensionNumber", "?")
    dur  = c.get("duration", 0)
    has_rec = bool(rec.get("id"))
    rec_id  = rec.get("id", "none")
    start   = c.get("startTime", "")
    print(f"  {start} | {name} ext={ext} | dur={dur}s | recording={has_rec} | recId={rec_id}")
