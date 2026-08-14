# SAD HEARTBEAT Snippet — Contracts Finder Pipeline

_Add this block to your SAD agent's HEARTBEAT.md to trigger the pipeline on the daily heartbeat cycle._

---

## Contracts Finder Pipeline Task (run at 06:00 daily, before other SAD tasks)

```
TASK: Contracts Finder Pipeline
TIME: 06:00 daily (5 minutes before main SAD heartbeat)
SCRIPT: python orchestrator.py --mode=incremental
WORKING_DIR: {openclaw_root}/agents/scraper/workspace/Inbound Data/contracts_finder/
LOG: contracts_finder/logs/orchestrator_{date}.log
```

### What it does
1. Queries Contracts Finder API for new disability lift / DFG contract notices since yesterday
2. Filters out irrelevant results (passenger lifts, maintenance contracts)
3. Enriches leads missing contact emails via Apollo.io
4. Pushes new contacts to HubSpot (creates contact + company, adds to Prospects list)
5. Enrols in HubSpot email sequence if HUBSPOT_SEQUENCE_ID is set
6. Runs engagement sweep to promote interested contacts

### SAD can also trigger ad-hoc via REQUESTS folder:

```python
# Paste this into SAD's context to trigger a manual run:
import json, pathlib, datetime

req = {
    "request_id": f"SAD_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}",
    "requested_by": "SAD",
    "mode": "incremental",
    "notes": "Manual trigger from SAD heartbeat"
}
pathlib.Path("REQUESTS").mkdir(exist_ok=True)
pathlib.Path(f"REQUESTS/contracts_finder_{req['request_id']}.json").write_text(json.dumps(req, indent=2))
print(f"Request written: {req['request_id']}")
```

### Outputs to check after each run
- `contracts_finder/contracts_finder_leads.csv` — latest leads snapshot
- `contracts_finder/logs/orchestrator_YYYY-MM-DD.log` — full run detail
- `contracts_finder/logs/pipeline_run_history.json` — run history (last 100 runs)
- HubSpot → Contacts → List: "Contracts Finder — Prospects"
