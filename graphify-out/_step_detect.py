import json
from graphify.detect import detect
from pathlib import Path

result = detect(Path(r"C:\Users\GilesHugo\SES_Workspace"))
Path("graphify-out/.graphify_detect.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

files = result.get("files", {})
print("TOTAL FILES:", result.get("total_files"))
print("TOTAL WORDS:", result.get("total_words"))
for k in ("code", "document", "paper", "image", "video"):
    v = files.get(k, [])
    if v:
        print(f"  {k}: {len(v)}")
print("SKIPPED SENSITIVE:", len(result.get("skipped_sensitive", []) or []))
print("SCAN ROOT:", result.get("scan_root"))
