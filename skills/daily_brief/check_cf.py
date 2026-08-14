import os, requests, json
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(r"C:\Users\GilesHugo\.hermes\.env"))
pat = os.environ["ASANA_PERSONAL_ACCESS_TOKEN"]
r = requests.get(
    "https://app.asana.com/api/1.0/custom_fields/1207160402234668",
    headers={"Authorization": f"Bearer {pat}"},
    params={"opt_fields": "name,enum_options.name,enum_options.gid"}
)
cf = r.json()["data"]
print(f"Field: {cf['name']}")
for opt in cf.get("enum_options", []):
    print(f"  {opt['gid']}  {opt['name']!r}")
