import csv, collections

base = r"C:\Users\GilesHugo\Downloads\usage_export_2026-09"

cost_by_model = collections.defaultdict(float)
cost_by_date = collections.defaultdict(float)
total_actual = 0.0
with open(base + r"\cost-2026-09-01_2026-09-21.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        c = float(row["cost"])
        cost_by_model[row["model"]] += c
        cost_by_date[row["start_time_iso"][:10]] += c
        total_actual += c

print("=== ACTUAL DeepSeek billed cost, 2026-09-01 .. 2026-09-21 ===")
for m, c in sorted(cost_by_model.items(), key=lambda x: -x[1]):
    print(f"  {m:20s} ${c:,.4f}")
print(f"  {'TOTAL':20s} ${total_actual:,.4f}")
n = len(cost_by_date)
print(f"  days covered: {n}")
print(f"  avg/day: ${total_actual/n:.4f}")
print(f"  x30.4 => month ${total_actual/n*30.4:,.2f}")
print(f"  x365 => year  ${total_actual/n*365:,.2f}")
print()

rates = collections.defaultdict(set)
vol = collections.defaultdict(float)
with open(base + r"\amount-2026-09-01_2026-09-21.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        if row["price"]:
            rates[(row["model"], row["type"])].add(float(row["price"]))
        if row["type"] != "request_count" and row["amount"]:
            vol[(row["model"], row["type"])] += float(row["amount"])

print("=== DISTINCT RATES in export (per token, USD) ===")
for (m, t), s in sorted(rates.items()):
    print(f"  {m:18s} {t:24s} {sorted(s)}")
print()
print("=== TOTAL VOLUME per model/type (Sep 1-21) ===")
for (m, t), v in sorted(vol.items()):
    print(f"  {m:18s} {t:24s} {int(v):,}")
