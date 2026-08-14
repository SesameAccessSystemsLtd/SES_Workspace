import json
from collections import Counter, defaultdict

data = json.load(open('memory/richard_comments_chunk_1.json', encoding='utf-8'))
assert len(data) == 381

# Category per comment index 0..380 (rows of 19, then final index 380)
rows = [
[10,10,2,2,10,4,10,3,10,10,10,6,3,10,10,10,10,8,1],
[10,9,1,10,4,10,10,10,6,3,2,10,7,6,3,8,10,10,7],
[10,10,1,10,3,9,10,10,10,10,10,6,10,10,5,1,10,8,10],
[10,10,4,10,10,3,10,10,8,10,10,10,4,2,5,8,3,5,10],
[10,1,4,2,10,10,2,8,10,6,6,6,2,10,10,10,10,7,10],
[6,8,10,9,10,2,4,10,10,10,10,6,10,10,10,6,10,10,10],
[10,6,10,10,10,10,10,10,10,6,10,6,10,10,10,10,10,10,10],
[1,8,3,10,4,10,10,10,3,10,10,4,3,10,3,3,8,10,10],
[2,1,10,6,10,10,10,10,10,10,10,1,10,10,6,1,2,2,6],
[10,10,10,6,2,6,10,6,8,10,6,8,4,10,10,6,10,10,10],
[6,10,6,10,10,10,6,1,1,6,10,10,10,6,2,10,10,10,10],
[10,7,10,6,1,4,10,10,10,10,10,2,4,10,10,10,2,10,6],
[10,8,3,10,2,10,10,10,10,10,10,10,10,10,10,5,10,6,6],
[10,10,10,10,6,6,10,10,4,3,10,3,3,8,10,10,2,1,10],
[6,10,10,10,10,10,10,6,6,6,10,1,10,10,10,10,10,10,1],
[10,1,10,1,7,7,4,4,10,4,9,10,10,10,10,1,8,10,1],
[8,8,9,4,4,8,10,10,10,10,10,10,10,4,4,10,10,1,8],
[10,10,1,7,10,10,6,1,2,10,2,8,10,10,10,10,10,10,10],
[8,8,9,10,10,4,10,10,10,10,10,7,10,10,10,10,10,10,10],
[8,10,10,8,10,10,8,8,10,6,10,10,6,10,8,8,10,10,10],
]
mapping = [x for row in rows for x in row]
mapping.append(4)  # index 380
assert len(mapping) == 381, len(mapping)

labels = {
    1: "1 QC check missed/not done",
    2: "2 Stock status not updated",
    3: "3 Wrong part/spec",
    4: "4 Incomplete Asana admin",
    5: "5 Order/invoice diligence failure",
    6: "6 Delayed/overdue",
    7: "7 Stock/ordering error",
    8: "8 Missing/incorrect documentation",
    9: "9 Communication/response gap",
    10: "10 Other/uncategorized",
}

counts = Counter(mapping)
print("=== PER-CATEGORY COUNTS (%d comments) ===" % len(data))
for k in range(1, 11):
    print("  %s: %d" % (labels[k], counts.get(k, 0)))
print("  TOTAL:", sum(counts.values()))

def author_group(a):
    if a is None:
        return "None/system (auto receipts/bots)"
    if a == "Giles":
        return "Giles"
    if a == "Richard":
        return "Richard"
    return "Others"

auth = Counter(author_group(d['comment_by']) for d in data)
print("\n=== AUTHOR BREAKDOWN ===")
for k in ("Giles", "Richard", "Others", "None/system (auto receipts/bots)"):
    print("  %s: %d" % (k, auth.get(k, 0)))
print("  Others detail:", dict(Counter(d['comment_by'] for d in data if d['comment_by'] not in ('Giles', 'Richard') and d['comment_by'])))

dates = [d['created_at'] for d in data if d.get('created_at')]
print("\n=== DATE RANGE ===")
print("  min:", min(dates))
print("  max:", max(dates))
years = sorted(Counter(dt[:4] for dt in dates).items())
print("  by year:", years)

annotated = []
for i, d in enumerate(data):
    a = dict(d)
    a['category'] = mapping[i]
    a['category_label'] = labels[mapping[i]].split(' ', 1)[1]
    annotated.append(a)
json.dump(annotated, open('dev_temp/richard_comments_categorized.json', 'w', encoding='utf-8'), indent=2)
print("\nSaved -> dev_temp/richard_comments_categorized.json")
