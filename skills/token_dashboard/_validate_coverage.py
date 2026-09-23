import csv, collections, json, datetime, os

base = r"C:\Users\GilesHugo\Downloads\usage_export_2026-09"

# Export volumes for Sep 1-21
exp = collections.defaultdict(float)
exp_cost = 0.0
with open(base + r"\amount-2026-09-01_2026-09-21.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        if row["type"] != "request_count" and row["amount"]:
            exp[row["type"]] += float(row["amount"])
with open(base + r"\cost-2026-09-01_2026-09-21.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        exp_cost += float(row["cost"])

print("EXPORT (actual, Sep 1-21):")
print(f"  cache_miss(in) : {int(exp['input_cache_miss_tokens']):,}")
print(f"  cache_hit(in)  : {int(exp['input_cache_hit_tokens']):,}")
print(f"  output         : {int(exp['output_tokens']):,}")
print(f"  billed cost    : ${exp_cost:,.4f}")
print()

# My model's Sep 1-21 volumes from token_spend.json (day buckets)
d = json.load(open(r"C:\Users\GilesHugo\SES_Workspace\data\token_spend.json"))
mine_in = mine_out = mine_cr = 0
mine_cost = 0.0
for day in d["day"]:
    k = day["k"]
    if "2026-09-01" <= k <= "2026-09-21":
        mine_in += day["in"]; mine_out += day["out"]; mine_cr += day["cr"]; mine_cost += day["cost"]

print("MY MODEL (state.db, Sep 1-21):")
print(f"  input(cache-miss): {mine_in:,}")
print(f"  cache-read(hit)  : {mine_cr:,}")
print(f"  output           : {mine_out:,}")
print(f"  modelled cost    : ${mine_cost:,.4f}")
print()
print("COVERAGE (mine / actual):")
print(f"  cache-miss: {mine_in/exp['input_cache_miss_tokens']*100:.1f}%")
print(f"  cache-hit : {mine_cr/exp['input_cache_hit_tokens']*100:.1f}%")
print(f"  output    : {mine_out/exp['output_tokens']*100:.1f}%")
print(f"  cost      : {mine_cost/exp_cost*100:.1f}%")
