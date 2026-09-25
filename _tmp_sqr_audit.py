import json, glob, os, sys

base = r"C:\Users\GilesHugo\SES_Workspace\data\email_archive\2026-09"
files = sorted(glob.glob(os.path.join(base, "*.json")))
hits = []
for f in files:
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    if "47335" in json.dumps(d):
        hits.append((f, d))

print("files matching 47335:", len(hits))
print("=" * 70)
for f, d in hits:
    print(os.path.basename(f)[:45])
    print("  subject :", d.get("subject"))
    s = d.get("sender") or d.get("from")
    print("  sender  :", s)
    print("  received:", d.get("received") or d.get("received_at") or d.get("date"))
    att = d.get("attachments")
    if att is None:
        print("  ATTACHMENTS: <absent>")
    else:
        print("  ATTACHMENTS:", type(att).__name__, "count=", len(att) if hasattr(att, '__len__') else "?")
        print("   ", json.dumps(att)[:700])
    # any key mentioning attach
    for k in d.keys():
        if "attach" in k.lower():
            print("   key:", k, "=", json.dumps(d[k])[:400])
    print("-" * 70)
