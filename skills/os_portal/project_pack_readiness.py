#!/usr/bin/env python3
"""per-project pack readiness report — reuses the orders-by-pack mapping.

Usage: python project_pack_readiness.py --project 1597 [--json]
Read-only. Readiness rule (Giles, 2026-09-14):
    expected_pack_readiness = latest(order delivery) + (3 days if the constraining
    order is a LASERCUT order, else 0).
Flags orders that are NOT confirmed (may slip).
"""
import argparse, datetime, sqlite3, sys, json
from pathlib import Path

WS = Path(r"C:/Users/GilesHugo/SES_Workspace")
sys.path.insert(0, str(WS / "skills" / "os_portal"))
import build_orders_by_pack as obp   # reuse the exact mapping/logic

_ORDER_META_CACHE = {}
def live_order_meta(order_gid):
    """Live (order_status, completed) for an order task. Cached per run."""
    if not order_gid:
        return "", False
    if order_gid in _ORDER_META_CACHE:
        return _ORDER_META_CACHE[order_gid]
    res = ("", False)
    try:
        d = obp._asana_get(f"/tasks/{order_gid}",
                           {"opt_fields": "completed,custom_fields.name,custom_fields.display_value"})
        if d:
            status = ""
            for cf in (d.get("custom_fields") or []):
                if (cf.get("name") or "").strip().lower() in ("order status", "ord status"):
                    status = (cf.get("display_value") or "").strip()
                    break
            res = (status, bool(d.get("completed")))
    except Exception:
        pass
    _ORDER_META_CACHE[order_gid] = res
    return res

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True, help="4-digit project code, e.g. 1597")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--detail", action="store_true", help="append the full per-order breakdown with URLs")
    ap.add_argument("--refresh", action="store_true", help="force-refresh the live hierarchy cache")
    args = ap.parse_args()
    proj = args.project.strip()
    today = datetime.date.today()

    if args.refresh:
        cache = WS / "data" / "os_status" / ".hier_cache.json"
        if cache.exists():
            cache.replace(cache.with_suffix(".json.bak"))
            print(f"[refresh] moved hierarchy cache aside -> full live re-resolve", file=sys.stderr)

    conn = sqlite3.connect(f"file:{obp.OPS_DB}?mode=ro", uri=True)
    packs = obp.load_incomplete_packs(conn)
    parts_by_pack = obp.load_parts_by_pack(conn)
    orders = obp.load_orders_by_part(conn)
    conn.close()

    # filter packs to this project (first 4 digits of pack ref)
    proj_packs = [p for p in packs if (p.get("pack_reference") or "").strip().startswith(proj)]

    # live hierarchy (lasercut = grandparent order)
    all_parts = [x for p in proj_packs for x in parts_by_pack.get(obp._norm_pack(p.get("asana_task_gid")), [])]
    try:
        hier = obp.build_hierarchy_index(all_parts)
    except Exception as e:
        hier = {}
        print(f"[warn] hierarchy resolve failed ({e})", file=sys.stderr)

    def d2s(d):
        return d.isoformat() if isinstance(d, datetime.date) else (str(d) if d else "")

    def parse(d):
        d = (d or "").strip()[:10]
        try: return datetime.datetime.strptime(d, "%Y-%m-%d").date()
        except Exception: return None

    out = []
    for p in proj_packs:
        pack_ref = (p.get("pack_reference") or p.get("pack_name") or "").strip().rstrip(".")
        pack_gid = p.get("asana_task_gid")
        build_due = parse(p.get("due_on"))
        raw = parts_by_pack.get(obp._norm_pack(pack_gid), [])
        cl = []
        for x in raw:
            c = obp.classify_part(x, orders.get(x.get("procurement_task_gid")))
            c["_hier"] = hier.get(x.get("asana_task_gid")) or {}
            cl.append(c)

        # gather OPEN orders feeding the pack — LIVE order due + status + URL
        o_rows = []
        for c in cl:
            h = c.get("_hier") or {}
            o = orders.get(c.get("procurement_task_gid")) if c.get("procurement_task_gid") else None
            ogid = h.get("order_gid") or (o or {}).get("asana_task_gid")
            oname = h.get("order_name") or (o or {}).get("task_name")
            onum = (o or {}).get("po_number")
            is_laser = bool(h.get("is_lasercut"))
            # LIVE due: prefer hierarchy's live order_due, else DB
            live_due = (h.get("order_due") or "").strip()[:10]
            db_due = ((o or {}).get("due_date") or "").strip()[:10]
            due = live_due or db_due
            due_src = "live" if live_due else ("db" if db_due else "")
            # LIVE order status + completed
            lstatus, ldone = live_order_meta(ogid) if ogid else ("", False)
            ostatus = lstatus or (o or {}).get("order_status") or c.get("order_status") or ""
            confirmed = bool(obp._CONFIRMED.search(ostatus)) if ostatus else False
            if not ogid:
                # no order link -> unordered part; blocker with no date
                o_rows.append({"part": c.get("sesame_pn"), "vendor": (c.get("vendor_name") or ""),
                               "order": None, "order_gid": None, "order_url": None,
                               "due": due, "src": due_src, "status": ostatus,
                               "confirmed": confirmed, "laser": is_laser, "done": ldone,
                               "bucket": c.get("_bucket")})
                continue
            o_rows.append({"part": c.get("sesame_pn"), "vendor": ((o or {}).get("vendor_name") or c.get("vendor_name") or ""),
                           "order": onum or oname, "order_gid": ogid,
                           "order_url": f"https://app.asana.com/1/{obp.ASANA_ORG}/task/{ogid}",
                           "due": due, "src": due_src, "status": ostatus, "confirmed": confirmed,
                           "laser": is_laser, "done": ldone, "bucket": c.get("_bucket")})

        # readiness = max over open orders of (delivery + 3d IF that order is
        # lasercut else delivery). "plus 3 days for lasercut parts only".
        adj = []
        for r in o_rows:
            if not r.get("due"):
                continue
            d = parse(r["due"])
            if d is None:
                continue
            rd = d + datetime.timedelta(days=3) if r.get("laser") else d
            adj.append((rd, r))
        readiness = None; rule = ""; constraining = None
        if adj:
            readiness, constraining = max(adj, key=lambda t: t[0])
            rule = "+3d (lasercut)" if constraining.get("laser") else "as-delivered"
        unconfirmed = [r for r in o_rows if r.get("order") and not r.get("confirmed")]
        no_order = [r for r in o_rows if not r.get("order")]

        out.append({
            "pack_ref": pack_ref, "pack_gid": pack_gid,
            "pack_link": f"https://app.asana.com/1/{obp.ASANA_ORG}/task/{pack_gid}",
            "build_due": d2s(build_due),
            "readiness": d2s(readiness), "rule": rule,
            "vs_build_days": (readiness - build_due).days if (readiness and build_due) else None,
            "n_orders": len([r for r in o_rows if r.get("order")]),
            "n_unconfirmed": len(unconfirmed),
            "n_no_order": len(no_order),
            "constraining": constraining, "orders": o_rows,
        })

    out.sort(key=lambda r: (r["readiness"] or "9999"))

    if args.json:
        print(json.dumps(out, indent=2, default=str)); return

    # ── COMPACT TABLE (default) — one row per pack + the key order(s) driving readiness ──
    def mmdd(dstr):
        if not dstr or len(dstr) < 10:
            return "—"
        m = {"01":"Jan","02":"Feb","03":"Mar","04":"Apr","05":"May","06":"Jun",
             "07":"Jul","08":"Aug","09":"Sep","10":"Oct","11":"Nov","12":"Dec"}[dstr[5:7]]
        return f"{int(dstr[8:10])} {m}"

    print(f"PROJECT {proj} — pack readiness  (readiness = latest order delivery +3d for lasercut only)  [{today}]")
    print("")
    print("| Pack | Build due | Expected ready | Rule | vs build | ⚠️ Slip risk | Driving order(s) |")
    print("|---|---|---|---|---|---|---|")
    for r in out:
        ready = mmdd(r["readiness"])
        vs = "no date"
        if r["readiness"]:
            if r["vs_build_days"] and r["vs_build_days"] > 0:
                vs = f"+{r['vs_build_days']}d LATE"
            else:
                vs = "on time"
        icon = ""
        if r["readiness"] and r["vs_build_days"] and r["vs_build_days"] > 0:
            icon = " 🔴" if r["vs_build_days"] >= 14 else " ⚠️"
        rule = r["rule"] or ("no orders" if not r["orders"] else "—")
        slip = f"{r['n_unconfirmed']} unconfirmed" if r["n_unconfirmed"] else "—"
        # driving order(s): the constraining one + any other order dated within 3d of it
        drivers = []
        if r.get("constraining"):
            c = r["constraining"]
            cname = str(c.get("order") or c.get("vendor") or "")[:26]
            lz = " [laser]" if c.get("laser") else ""
            cf = "" if c.get("confirmed") else " ⚠unconf"
            drivers.append(f"{cname} ({mmdd(c.get('due'))}){lz}{cf}")
        drv = "; ".join(drivers) or "—"
        print(f"| {r['pack_ref']} | {mmdd(r['build_due']) or '—'} | {ready}{icon} | {rule} | {vs} | {slip} | {drv} |")
    print("")
    print("_expected ready = latest order delivery + 3 days where the constraining order is LASERCUT._")

    if args.detail:
        print("\n" + "=" * 100)
        for r in out:
            print(f"\n{r['pack_ref']}  build {r['build_due'] or '-'} | ready {r['readiness'] or '-'}")
            print(f"  pack: {r['pack_link']}")
            for o in r["orders"]:
                if o.get("order"):
                    c = "confirmed" if o["confirmed"] else "UNCONFIRMED"
                    lz = " [laser]" if o["laser"] else ""
                    print(f"    due {o['due'] or '-':10} {c:11}{lz} {str(o['vendor'])[:20]:20} {str(o['order'])[:30]}")
                    if o.get("order_url"):
                        print(f"        {o['order_url']}")
                else:
                    print(f"    NO ORDER LINK | {str(o['part'])[:30]}")

if __name__ == "__main__":
    main()
