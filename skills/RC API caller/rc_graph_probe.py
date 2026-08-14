"""
rc_graph_probe.py — test Graph API access to rc-notes shared mailbox
using existing Azure credentials from .env
"""
import os, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / ".env")

tenant_id     = os.getenv("PURCHASING_TENANT_ID")
client_id     = os.getenv("PURCHAISNG_SECRET_ID")   # UUID — likely app ID
client_secret = os.getenv("PURCHASING_EMAIL_API_KEY")
MAILBOX       = "rc-notes@sesameaccess.onmicrosoft.com"

print(f"tenant_id     = {tenant_id}")
print(f"client_id     = {client_id}")
print(f"client_secret = {client_secret[:6]}..." if client_secret else "client_secret = None")
print()

# ── Get token (app-only / client credentials) ─────────────────────────────────
token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
resp = requests.post(token_url, data={
    "grant_type":    "client_credentials",
    "client_id":     client_id,
    "client_secret": client_secret,
    "scope":         "https://graph.microsoft.com/.default",
}, timeout=30)

print(f"Token request status: {resp.status_code}")
if resp.status_code != 200:
    print("BODY:", resp.text[:500])
    raise SystemExit(1)

token = resp.json()["access_token"]
print("Token OK\n")

# ── What permissions does this token have? ────────────────────────────────────
# Decode claims (base64 middle segment) without verifying signature
import base64, json as _json
parts = token.split(".")
padded = parts[1] + "=" * (4 - len(parts[1]) % 4)
claims = _json.loads(base64.b64decode(padded))
print("Granted roles (app permissions):", claims.get("roles", []))
print()

# ── Try reading shared mailbox ────────────────────────────────────────────────
hdrs = {"Authorization": f"Bearer {token}"}
r = requests.get(
    f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages",
    headers=hdrs,
    params={"$top": 5, "$orderby": "receivedDateTime desc", "$select": "subject,from,receivedDateTime,bodyPreview"},
    timeout=30,
)
print(f"Mailbox read status: {r.status_code}")
if r.status_code == 200:
    msgs = r.json().get("value", [])
    print(f"Messages found: {len(msgs)}")
    for m in msgs:
        print(f"  {m.get('receivedDateTime','')} | from={m.get('from',{}).get('emailAddress',{}).get('address')} | {m.get('subject','')[:60]}")
else:
    print("ERROR:", r.text[:600])
