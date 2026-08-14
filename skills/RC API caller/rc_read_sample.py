"""Read full body of RC notes emails to understand parse format."""
import os, requests, base64, json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / ".env")

tenant_id     = os.getenv("PURCHASING_TENANT_ID")
client_id     = os.getenv("PURCHAISNG_SECRET_ID")
client_secret = os.getenv("PURCHASING_EMAIL_API_KEY")
MAILBOX       = "rc-notes@sesameaccess.onmicrosoft.com"

resp = requests.post(
    f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
    data={"grant_type": "client_credentials", "client_id": client_id,
          "client_secret": client_secret, "scope": "https://graph.microsoft.com/.default"},
    timeout=30,
)
resp.raise_for_status()
token = resp.json()["access_token"]
hdrs = {"Authorization": f"Bearer {token}"}

msgs = requests.get(
    f"https://graph.microsoft.com/v1.0/users/{MAILBOX}/messages",
    headers=hdrs,
    params={"$top": 5, "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,receivedDateTime,body"},
    timeout=30,
).json().get("value", [])

for i, m in enumerate(msgs):
    print(f"\n{'='*60}")
    print(f"EMAIL {i+1}: {m.get('subject','')}")
    print(f"From: {m.get('from',{}).get('emailAddress',{}).get('address')}")
    print(f"Date: {m.get('receivedDateTime')}")
    print(f"{'='*60}")
    body = m.get("body", {})
    content_type = body.get("contentType", "")
    content = body.get("content", "")
    print(f"ContentType: {content_type}")
    if content_type == "html":
        # Strip HTML tags crudely for preview
        import re
        text = re.sub(r'<[^>]+>', ' ', content)
        text = re.sub(r'&nbsp;', ' ', text)
        text = re.sub(r'&amp;', '&', text)
        text = re.sub(r'\s+', ' ', text).strip()
        print(text[:3000])
    else:
        print(content[:3000])
