import json, re
from pathlib import Path

d = json.loads(Path("graphify-out/.graphify_detect.json").read_text(encoding="utf-8"))
root = d["scan_root"]
docs = d.get("files", {}).get("document", [])
papers = d.get("files", {}).get("paper", [])
src = docs + papers

# look for logs/handoffs/plans/reports
pats = {
    "LOG": r"_log|log_",
    "HANDOFF": r"handoff",
    "PLAN": r"plan",
    "REPORT": r"report",
    "README": r"readme",
    "CAPSULE": r"capsule",
    "SKILL": r"skill",
}
counts = {k: 0 for k in pats}
for f in src:
    n = str(f).replace("\\", "/").split("/")[-1].lower()
    for k, p in pats.items():
        if re.search(p, n):
            counts[k] += 1
for k, v in counts.items():
    print(f"{k}: {v}")

# size distribution
sizes = []
for f in src:
    try:
        sizes.append((Path(f).stat().st_size, str(f)))
    except Exception:
        pass
sizes.sort(reverse=True)
print("--- total docs:", len(src))
over100k = [s for s in sizes if s[0] > 100_000]
print("docs >100KB:", len(over100k), "total bytes >100KB:", sum(s[0] for s in over100k))
print("--- top 12 largest ---")
for sz, f in sizes[:12]:
    rel = str(f).replace(root + "\\", "").replace(root + "/", "")
    print(f"{sz//1024:6d}KB  {rel[:110]}")
print("--- distribution ---")
buckets = {"<10KB": 0, "10-50KB": 0, "50-100KB": 0, ">100KB": 0}
for sz, _ in sizes:
    if sz < 10_000: buckets["<10KB"] += 1
    elif sz < 50_000: buckets["10-50KB"] += 1
    elif sz < 100_000: buckets["50-100KB"] += 1
    else: buckets[">100KB"] += 1
print(buckets)
