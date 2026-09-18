#!/usr/bin/env python3
"""demo_pack_parts_orders.py — CHG-2D (HITL-gated). READ-ONLY demonstration.

Proves the PART-LEVEL pack->vendor/order join on ONE pack (1037.1016.BS.2, job 1037)
without touching production renderers. Joins:

    order Asana task --subtasks--> [sesame part ids]
    sesame part id --parts_control.build_assembly_pack_gid--> build pack

so an order IMPACTS a pack IFF one of its subtask parts maps to that pack.
This is the fix for the naive job-level attribution (which over-shows ~9 orders
when only ~2 genuinely feed the pack).

Read-only: Asana GETs + ops.db read. No Asana writes, no ops.db writes. Does NOT
rewrite build_os_status.orders_by_job (that stays the labelled job-level fallback).

Usage:
    python demo_pack_parts_orders.py [--job 1037] [--pack 1037.1016.BS.2] [--out PATH]
"""

import argparse
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
OPS_DB = r"C:/Users/GilesHugo/SES_Workspace/db/ops.db"
DEFAULT_OUT = WS / "data" / "os_status" / "review" / "pack_parts_orders_1037_BS2.md"

sys.path.insert(0, str(WS / "skills" / "os_portal"))
from build_os_status import paginate, ORDER_NUMBERS, ASANA_TASK_BASE  # noqa: E402


def _norm(pn):
    return (pn or "").strip().lower().rstrip(".")


def _pack_ref_norm(pack_ref):
    """Normalise a pack reference ('1037.1016.BS.2.' -> '1037.1016.bs.2')."""
    return (pack_ref or "").strip().lower().rstrip(".")


def load_parts_control():
    """Return (part_to_pack, pack_parts, null_pack_finish).

    part_to_pack     : {normalised sesame_pn: normalised pack_ref}
    pack_parts       : {normalised pack_ref: [normalised sesame_pn, ...]}
    null_pack_finish : [ (sesame_pn, vendor_name) ] finish/process rows with NULL pack
    """
    FINISH = {"powder coating", "engraving", "laser cut", "lasercut", "machining",
              "anodising", "electroplating", "galvanising", "marine lacquer",
              "yellow zinc plating", "laser cut/machining"}
    conn = sqlite3.connect(f"file:{OPS_DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    part_to_pack = {}
    pack_parts = defaultdict(list)
    null_pack_finish = []
    for r in conn.execute(
            "SELECT sesame_pn, vendor_name, build_assembly_pack_gid FROM parts_control"):
        pn = _norm(r["sesame_pn"])
        vendor = (r["vendor_name"] or "").strip().lower()
        pack_ref = r["build_assembly_pack_gid"]
        if pack_ref:
            pr = _pack_ref_norm(pack_ref)
            part_to_pack[pn] = pr
            pack_parts[pr].append(pn)
        elif vendor in FINISH:
            null_pack_finish.append((r["sesame_pn"] or "", r["vendor_name"] or ""))
    conn.close()
    return part_to_pack, pack_parts, null_pack_finish


def extract_sesame_pn(subtask):
    """Sesame Part No from the custom field, else parse the task name."""
    for cf in subtask.get("custom_fields", []) or []:
        if (cf.get("name") or "").strip().lower() in ("sesame part no", "sesame part number"):
            v = (cf.get("display_value") or cf.get("text_value") or "").strip()
            if v:
                return v
    name = subtask.get("name") or ""
    m = re.search(r"\b(\d{4}\.\s*\d{1,4}(?:[.\w()\-]*)?)", name)
    if m:
        return re.sub(r"\s+", "", m.group(1))
    return ""


def main():
    ap = argparse.ArgumentParser(description="CHG-2D part-level pack->order demo (read-only)")
    ap.add_argument("--job", default="1037")
    ap.add_argument("--pack", default="1037.1016.BS.2")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args()

    target_pack = _pack_ref_norm(args.pack)
    job = args.job

    part_to_pack, pack_parts, null_pack_finish = load_parts_control()
    pack_part_pns = set(pack_parts.get(target_pack, []))
    # finish/process parts that WOULD belong to this pack once CHG-3A lands
    unresolved_finish = [pn for pn, v in null_pack_finish
                         if _norm(pn).startswith(f"{job}.") and _norm(pn) in pack_part_pns or
                         _norm(pn).startswith(f"{job}.1016")]

    orders = paginate(
        f"/projects/{ORDER_NUMBERS}/tasks",
        {"opt_fields": "name,gid,completed,due_on,tags.name"})
    job_orders = [o for o in orders if re.search(rf"-{job}\b", o.get("name") or "")]

    lines = []
    lines.append(f"# Pack → order join demo — {args.pack} (job {job})")
    lines.append("")
    lines.append(f"Pack ref: `{target_pack}` · {len(pack_part_pns)} part(s) in parts_control.")
    lines.append("Method: order task → subtasks (Sesame Part No) → parts_control part→pack.")
    lines.append("")
    lines.append(f"## Orders for job {job} ({len(job_orders)}) — which genuinely impact this pack")
    lines.append("")
    lines.append("| Order | Vendor | Parts in this pack | Impacts pack? |")
    lines.append("|---|---|---|---|")

    impacting = []
    for o in job_orders:
        name = o.get("name") or ""
        m = re.match(r"^(\d{5,6})-(\d{4})", name)
        po = m.group(1) if m else ""
        vendor = "-".join(name.split("-")[2:]).strip() if m else ""
        subs = paginate(f"/tasks/{o['gid']}/subtasks",
                        {"opt_fields": "name,gid,completed,custom_fields.name,"
                                       "custom_fields.display_value"})
        hit_parts = []
        for s in subs:
            pn = _norm(extract_sesame_pn(s))
            if pn and pn in pack_part_pns:
                hit_parts.append(pn)
        if hit_parts:
            impacting.append(o)
            status = f"**YES** ({len(hit_parts)} part(s))"
            parts_txt = ", ".join(hit_parts)
        else:
            status = "no"
            parts_txt = ""
        done = " (completed)" if o.get("completed") else ""
        lines.append(f"| {po} · {vendor[:40]}{done} | {vendor[:40]} | {parts_txt} | {status} |")

    lines.append("")
    lines.append(f"## Conclusion")
    lines.append("")
    lines.append(f"Of **{len(job_orders)}** job-level orders, the part-level join attributes "
                 f"**{len(impacting)}** as genuinely impacting `{target_pack}`:")
    for o in impacting:
        name = o.get("name") or ""
        lines.append(f"- [{name}]({ASANA_TASK_BASE}/{o['gid']})")
    lines.append("")
    if unresolved_finish:
        lines.append("### Unresolved finish parts (pending CHG-3A pack inheritance)")
        lines.append("These finish/process parts have NULL `build_assembly_pack_gid`, so they "
                     "cannot yet be attributed to this pack:")
        for pn in sorted(set(unresolved_finish))[:20]:
            lines.append(f"- `{pn}`")
        lines.append("")
    lines.append("_Read-only demo — no Asana/DB writes. Production `orders_by_job` left "
                 "unchanged as the labelled job-level fallback._")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"WROTE {out} ({len(impacting)} impacting order(s) of {len(job_orders)} job orders)")


if __name__ == "__main__":
    main()
