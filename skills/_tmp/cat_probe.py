"""READ-ONLY: what Outlook categories are used in the purchasing mailbox (last N days),
and how do Confirmation/Quote/Invoice categories break down. No writes. Graph Mail.Read."""
import os, sys, datetime, collections, requests

home = os.path.expanduser("~")
env = {}
for line in open(os.path.join(home, ".hermes", ".env")):
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")

TENANT = env["PURCHASING_TENANT_ID"]; CLIENT = env["PURCHAISNG_CLIENT_ID"]; SECRET = env["PURCHASING_API_KEY"]
MB = "purchasing@sesameaccess.com"
WINDOW_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60

tok = requests.post(f"https://login.microsoftonline.com/{TENANT}/oauth2/v2.0/token",
    data={"grant_type": "client_credentials", "client_id": CLIENT, "client_secret": SECRET,
          "scope": "https://graph.microsoft.com/.default"}, timeout=30).json()["access_token"]
h = {"Authorization": f"Bearer {tok}"}
BASE = f"https://graph.microsoft.com/v1.0/users/{MB}"
since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")

def get_all(path, params):
    out = []
    while True:
        r = requests.get(path, headers=h, params=params, timeout=30)
        if r.status_code != 200:
            return out, r.status_code, r.text[:200]
        j = r.json(); out += (j.get("value") or [])
        nxt = j.get("@odata.nextLink")
        if not nxt: return out, 200, ""
        path, params = nxt, None

# Inbox messages with categories
msgs, sc, err = get_all(f"{BASE}/mailFolders/inbox/messages",
    {"$top": 100, "$filter": f"receivedDateTime ge {since}",
     "$select": "subject,categories,receivedDateTime,from", "$orderby": "receivedDateTime desc"})
print(f"INBOX since {since[:10]}: {len(msgs)} (HTTP {sc}) {err}")

cat_counts = collections.Counter()
categorised = 0
for m in msgs:
    cats = m.get("categories") or []
    if cats:
        categorised += 1
    for c in cats:
        cat_counts[c] += 1

print(f"\nMessages with >=1 category: {categorised}/{len(msgs)}")
print("\n=== CATEGORY COUNTS (inbox, window) ===")
for c, n in cat_counts.most_common():
    print(f"  {n:4}  {c}")

# Sample of confirmation-categorised subjects
print("\n=== Sample: 'Confirmation'-categorised subjects ===")
shown = 0
for m in msgs:
    cats = [c.lower() for c in (m.get("categories") or [])]
    if any("confirm" in c for c in cats) and shown < 15:
        frm = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "")
        print(f"  [{m.get('receivedDateTime','')[:10]}] {m.get('subject','')[:60]:60} <- {frm}")
        shown += 1
if shown == 0:
    print("  (none)")
