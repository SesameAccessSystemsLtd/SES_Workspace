import csv, collections, json

base = r"C:\Users\GilesHugo\Downloads\usage_export_2026-09"

# Exact rates per model/type (off-peak). Peak = 2x.
rate = {}
with open(base + r"\amount-2026-09-01_2026-09-21.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        if row["price"]:
            k = (row["model"], row["type"])
            rate.setdefault(k, set()).add(float(row["price"]))
print("Confirmed rate cards (off-peak / peak):")
for k in sorted(rate):
    print(f"  {k[0]:18s} {k[1]:24s} {sorted(rate[k])}")

# Map my model names -> export billing names.
# deepseek-chat  -> deepseek-v4-flash  (per DeepSeek: legacy name routes to Flash line)
# deepseek-v4-pro -> deepseek-v4-pro
FLASH_MISS = 2.2e-7
FLASH_HIT  = 7e-9
FLASH_OUT  = 6.6e-7

d = json.load(open(r"C:\Users\GilesHugo\SES_Workspace\data\token_spend.json"))
sep_in = sep_cr = sep_out = sep_cost = 0
for day in d["day"]:
    if "2026-09-01" <= day["k"] <= "2026-09-21":
        sep_in += day["in"]; sep_cr += day["cr"]; sep_out += day["out"]; sep_cost += day["cost"]

# Cost my Sep volumes at the OFF-PEAK flash card (my model uses peak-aware, so mine is a bit higher)
myvol_offpeak = sep_in*FLASH_MISS + sep_cr*FLASH_HIT + sep_out*FLASH_OUT
print()
print(f"My Sep 1-21 modelled cost (peak-aware)      : ${sep_cost:.4f}")
print(f"My Sep 1-21 volumes @ off-peak flash card   : ${myvol_offpeak:.4f}")

exp_cost = 0.0
with open(base + r"\cost-2026-09-01_2026-09-21.csv", encoding="utf-8-sig") as f:
    for row in csv.DictReader(f):
        exp_cost += float(row["cost"])
print(f"ACTUAL billed Sep 1-21                       : ${exp_cost:.4f}")
print()
print(f"=> My model covers {sep_cost/exp_cost*100:.1f}% of actual billed cost.")
print(f"=> Ratio actual:model = {exp_cost/sep_cost:.2f}x")
print(f"=> True monthly (actual x 30.4/21) = ${exp_cost/21*30.4:.2f}")
print(f"=> True annual  (actual x 365/21)  = ${exp_cost/21*365:.2f}")
