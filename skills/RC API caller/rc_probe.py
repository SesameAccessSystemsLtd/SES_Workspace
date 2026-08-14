"""
rc_probe.py — Discover what transcript/AI data is accessible with current credentials.
Tries: call log detailed view, AI note endpoints, subscription list.
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
print("=== AUTH OK ===\n")

# ── 1. Today's calls with FULL detail ─────────────────────────────────────────
print("=== 1. CALL LOG (Detailed view, today) ===")
status, r = get(token, f"{server}/restapi/v1.0/account/~/call-log",
    {"type": "Voice", "view": "Detailed", "dateFrom": "2026-06-25T00:00:00Z", "perPage": 5})
print(f"Status: {status}")
if status == 200:
    records = r.json().get("records", [])
    print(f"Records: {len(records)}")
    if records:
        # Print all top-level keys of first record to see what fields exist
        print("Fields on first record:", list(records[0].keys()))
        # Print full first record
        print(json.dumps(records[0], indent=2)[:3000])
else:
    print(r.text[:500])

print()

# ── 2. Try AI Notes endpoint (RC AI Assistant / Smart Notes) ──────────────────
print("=== 2. AI NOTES ENDPOINTS ===")
ai_paths = [
    "/ai/note/v1/notes",
    "/ai/speech/v1/async/jobs",
    "/rcvideo/v1/account/~/recordings",
    "/restapi/v1.0/account/~/call-log?notes=true",
]
for path in ai_paths:
    url = f"{server}{path}" if path.startswith("/") else path
    status, r = get(token, url)
    print(f"  {path} → {status}")
    if status == 200:
        print(f"    KEYS: {list(r.json().keys()) if isinstance(r.json(), dict) else 'array'}")
        print(f"    {str(r.json())[:300]}")

print()

# ── 3. List existing webhook subscriptions ────────────────────────────────────
print("=== 3. EXISTING SUBSCRIPTIONS ===")
status, r = get(token, f"{server}/restapi/v1.0/subscription")
print(f"Status: {status}")
if status == 200:
    subs = r.json().get("records", [])
    print(f"Subscriptions found: {len(subs)}")
    for s in subs:
        print(f"  id={s.get('id')} status={s.get('status')} filters={s.get('eventFilters', [])}")
else:
    print(r.text[:500])

print()

# ── 4. Try per-extension call log (own extension) ─────────────────────────────
print("=== 4. OWN EXTENSION CALL LOG (detailed) ===")
status, r = get(token, f"{server}/restapi/v1.0/account/~/extension/~/call-log",
    {"type": "Voice", "view": "Detailed", "dateFrom": "2026-06-25T00:00:00Z", "perPage": 3})
print(f"Status: {status}")
if status == 200:
    records = r.json().get("records", [])
    print(f"Records: {len(records)}")
    if records:
        print(json.dumps(records[0], indent=2)[:2000])
else:
    print(r.text[:500])
