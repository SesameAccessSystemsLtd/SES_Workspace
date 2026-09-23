import json
from collections import Counter
from pathlib import Path

d = json.loads(Path("graphify-out/.graphify_detect.json").read_text(encoding="utf-8"))
root = d["scan_root"].rstrip("\\/")
files = []
for k in ("code", "document", "paper", "image", "video"):
    files.extend(d.get("files", {}).get(k, []))

c = Counter()
for f in files:
    p = str(f).replace("\\", "/")
    r = root.replace("\\", "/")
    if p.startswith(r + "/"):
        rel = p[len(r) + 1:]
    else:
        rel = p
    if rel.startswith("graphify-out/"):
        continue
    parts = rel.split("/")
    c[parts[0] if len(parts) > 1 else "(root)"] += 1

for name, n in c.most_common(12):
    print(f"{n:6d}  {name}")
print("---")
print("total counted:", sum(c.values()))
