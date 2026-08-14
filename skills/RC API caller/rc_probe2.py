"""
rc_probe2.py — probe AI note endpoints and list subscriptions
"""
import os, base64, json, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / ".env")

client_id     = os.getenv("RC_CLIENT_ID")
client_secret = os.getenv("RC_CLIENT_SECRET")
jwt           = os.getenv("RC_JWT")
server        = os.getenv("RC_SERVER")

def auth():
    creds = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        f"{server}/restapi/oauth/token",
        headers={"Authorization": f"Basic {creds}", "Content-Type": "application/x-www-form-urlencoded"},
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]

def get(token, url, params=None):
    r = requests.get(url, headers={"Authorization": f"Bearer {token}"}, params=params, timeout=30)
    return r.status_code, r

token = auth()
print("AUTH OK\n")

# ── AI endpoints ──────────────────────────────────────────────────────────────
print("=== AI ENDPOINTS ===")
ai_paths = [
    "/ai/note/v1/notes",
    "/ai/speech/v1/async/jobs",
    "/ai/ringsense/v1/public/accounts/~/domains/pbx/records",
    "/rcvideo/v1/account/~/recordings",
    "/restapi/v1.0/account/~/telephony/sessions",
]
for path in ai_paths:
    status, r = get(token, f"{server}{path}")
    print(f"  {path}")
    print(f"    status={status}")
    if status == 200:
        d = r.json()
        print(f"    keys={list(d.keys()) if isinstance(d, dict) else 'array'}")
        print(f"    preview={str(d)[:200]}")
    elif status != 404:
        print(f"    body={r.text[:200]}")
    print()

# ── Subscriptions ─────────────────────────────────────────────────────────────
print("=== SUBSCRIPTIONS ===")
status, r = get(token, f"{server}/restapi/v1.0/subscription")
print(f"status={status}")
if status == 200:
    subs = r.json().get("records", [])
    print(f"count={len(subs)}")
    for s in subs:
        print(f"  id={s.get('id')}")
        print(f"  status={s.get('status')}")
        print(f"  filters={s.get('eventFilters', [])}")
        print(f"  deliveryMode={s.get('deliveryMode', {}).get('transportType')}")
        print()
else:
    print(r.text[:500])
