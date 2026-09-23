import json
from collections import Counter
from pathlib import Path

d = json.loads(Path("graphify-out/.graphify_detect.json").read_text(encoding="utf-8"))
docs = d.get("files", {}).get("document", [])

# by top-2 dir for docs
c = Counter()
for f in docs:
    parts = f.replace("\\", "/").split("/")
    try:
        i = parts.index("SES_Workspace")
        rel = parts[i + 1:]
    except ValueError:
        rel = parts[-2:]
    if rel and rel[0] == "graphify-out":
        continue
    key = "/".join(rel[:2]) if len(rel) > 2 else "/".join(rel)
    c[key] += 1

print("=== DOCS by dir ===")
for k, v in c.most_common(25):
    print(f"{v:5d}  {k}")
