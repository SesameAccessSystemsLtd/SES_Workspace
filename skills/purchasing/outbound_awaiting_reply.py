"""READ-ONLY probe: outbound enquiries awaiting a reply (purchasing mailbox).
For each Sent Item in the window, check whether an INBOUND reply exists in the same
conversation AFTER the send. Flag those sent >= MIN_DAYS ago with NO reply.
No writes. Graph Mail.Read only.
"""
import os, sys, datetime, requests, collections

home = os.path.expanduser("~")
env = {}
for line in open(os.path.join(home, ".hermes", ".env")):
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1); env[k.strip()] = v.strip().strip('"').strip("'")

TENANT = env["PURCHASING_TENANT_ID"]; CLIENT = env["PURCHAISNG_CLIENT_ID"]; SECRET = env["PURCHASING_API_KEY"]
MB = "purchasing@sesameaccess.com"
MIN_DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 3
WINDOW_DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 24

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

# 1) outbound sent items (exclude drafts/auto)
sent, sc, err = get_all(f"{BASE}/mailFolders/sentitems/messages",
    {"$top": 100, "$filter": f"sentDateTime ge {since}", "$select":
     "id,subject,toRecipients,sentDateTime,conversationId,webLink", "$orderby": "sentDateTime desc"})
print(f"Sent items since {since[:10]}: {len(sent)} (HTTP {sc}) {err}")

# 2) conversation index: any INBOUND message per conversation after the send
def convo_has_reply(conv_id, after_iso):
    msgs, code, _ = get_all(f"{BASE}/messages",
        {"$filter": f"conversationId eq '{conv_id}'", "$select": "subject,from,sentDateTime,receivedDateTime,conversationId",
         "$top": 50})
    for m in msgs:
        got = m.get("receivedDateTime") or m.get("sentDateTime") or ""
        frm = ((m.get("from") or {}).get("emailAddress") or {}).get("address", "").lower()
        if got and got > after_iso and "sesameaccess.com" not in frm:
            return True, frm, got
    return False, "", ""

now = datetime.datetime.now(datetime.timezone.utc)
awaiting = []
for m in sent:
    subj = m.get("subject") or ""
    sd = m.get("sentDateTime") or ""
    if not sd: continue
    age = (now - datetime.datetime.strptime(sd[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)).days
    if age < MIN_DAYS: continue
    to = ", ".join(x["emailAddress"]["address"] for x in m.get("toRecipients", []))
    reply, rfrm, rgot = convo_has_reply(m.get("conversationId"), sd)
    if not reply:
        awaiting.append((age, sd[:10], subj, to, m.get("webLink", "")))

awaiting.sort(reverse=True)
print(f"\n=== OUTBOUND with NO reply after >= {MIN_DAYS} days: {len(awaiting)} ===")
for age, d, subj, to, link in awaiting:
    print(f"  {age:3}d | {d} | {subj[:55]:55} -> {to[:40]}")
