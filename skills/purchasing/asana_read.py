"""
asana_read.py — READ-ONLY Asana access for the purchasing gateway.

Only GET requests. Writes stay with SAP via queues/REQUESTS (estate rule).
Used by the invoice gate to verify a task's live fields at decision time
(Signed OFF, Invoice Number, Carriage, Price, ORDER Status) rather than trusting
the nightly mirror, and by the nightly mover to see whether a human has signed off.
"""
from __future__ import annotations

import os
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[3] / ".hermes" / ".env", override=True)

TOKEN = os.getenv("ASANA_PERSONAL_ACCESS_TOKEN")
BASE = "https://app.asana.com/api/1.0"

FIELD = {  # custom field GIDs (Order Numbers project)
    "order_status": "1143955334207040",
    "price_ex_vat": "854330340727040",
    "carriage_ex_vat": "861798159849607",
    "invoice_number": "855654251787627",
    "signed_off": "887966711229634",
    "vendor": "854127216887083",
    "inc_vat": "1204759087326205",
    "sqr": "1211774738554014",
    "payment": "854977725253177",            # Account (DEFAULT) / Credit Card / Pro-Forma Invoice / PayPal ...
    "mapped_using_ai": "1218748889949686",   # 100% / Requires HITL / NOT MAPPED  (Invoices project only)
}
SIGNED_OFF_HUMAN = "Signed Off "
SIGNED_OFF_AI = "Signed off by AI - AWAITING APPROVAL "


def _headers():
    if not TOKEN:
        raise RuntimeError("ASANA_PERSONAL_ACCESS_TOKEN missing from .hermes/.env")
    return {"Authorization": f"Bearer {TOKEN}", "Accept": "application/json"}


def get_task_fields(gid: str) -> dict | None:
    """Live snapshot of the fields the gate cares about. None if the task can't be read."""
    r = requests.get(f"{BASE}/tasks/{gid}", headers=_headers(), timeout=20,
                     params={"opt_fields": "name,completed,custom_fields.gid,custom_fields.name,"
                                           "custom_fields.display_value,custom_fields.number_value"})
    if r.status_code != 200:
        return None
    d = r.json().get("data", {})
    out = {"gid": gid, "name": d.get("name", ""), "completed": bool(d.get("completed"))}
    by_gid = {cf.get("gid"): cf for cf in d.get("custom_fields", [])}
    for key, fgid in FIELD.items():
        cf = by_gid.get(fgid)
        if not cf:
            out[key] = None
            continue
        out[key] = cf.get("number_value") if cf.get("number_value") is not None else cf.get("display_value")
    return out


def search_invoice_number(workspace_gid: str, invoice_number: str) -> list[dict]:
    """Tasks whose Invoice Number field contains this number (duplicate guard). Premium search API."""
    r = requests.get(f"{BASE}/workspaces/{workspace_gid}/tasks/search", headers=_headers(), timeout=20,
                     params={f"custom_fields.{FIELD['invoice_number']}.contains": invoice_number,
                             "opt_fields": "name,completed", "limit": 10})
    if r.status_code != 200:
        return []
    return r.json().get("data", [])
