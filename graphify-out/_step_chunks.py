import os, json
from pathlib import Path

unc = [l.strip() for l in open("graphify-out/.graphify_uncached.txt", encoding="utf-8") if l.strip()]
huge = sorted([f for f in unc if os.path.getsize(f) > 100_000])
small = sorted(f for f in unc if os.path.getsize(f) <= 100_000)

chunks = [huge[i:i+8] for i in range(0, len(huge), 8)]
cur = []
for f in small:
    cur.append(f)
    if len(cur) == 22:
        chunks.append(cur); cur = []
if cur:
    chunks.append(cur)

cdir = Path("graphify-out/_chunks")
cdir.mkdir(exist_ok=True)
for i, ch in enumerate(chunks, 1):
    (cdir / f"chunk_{i:02d}.txt").write_text("\n".join(ch), encoding="utf-8")

print(f"huge: {len(huge)} files -> {len([c for c in chunks if len(c)<=8 and c and os.path.getsize(c[0])>100000])} chunks")
print(f"small: {len(small)} files")
print(f"TOTAL CHUNKS: {len(chunks)}")
for i, ch in enumerate(chunks, 1):
    sz = sum(os.path.getsize(f) for f in ch)
    print(f"  chunk_{i:02d}: {len(ch):3d} files, {sz//1024:8d}KB")
