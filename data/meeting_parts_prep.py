import os, requests, collections, time

home = os.path.expanduser("~"); tok=None
for ep in [os.path.join(home,".hermes",".env"), os.path.join(home,".openclaw",".env")]:
    try:
        for line in open(ep):
            if line.startswith("ASANA_PERSONAL_ACCESS_TOKEN"):
                tok=line.split("=",1)[1].strip().strip('"').strip("'")
    except FileNotFoundError: pass
    if tok: break
h={"Authorization":f"Bearer {tok}"}

PARTS="1211660707080749"   # Parts Control
# fields we care about
FIELDS=["Sesame Part No","Order Status","Order Status ","STOCK ACTION","Vendor","Due Date","ORDER QTY","Arrived"]

def paged(url, params):
    out=[]
    while True:
        r=requests.get(url,headers=h,params=params,timeout=30)
        if r.status_code!=200: return out, r.status_code
        j=r.json(); out+=(j.get("data") or [])
        off=(j.get("next_page") or {}).get("offset")
        if not off: return out,200
        params=dict(params); params["offset"]=off

tasks,sc=paged(f"https://app.asana.com/api/1.0/projects/{PARTS}/tasks",
               {"completed":"false","limit":100,
                "opt_fields":"name,due_on,custom_fields.name,custom_fields.display_value,assignee.name"})
print("open parts in Parts Control:", len(tasks), "HTTP", sc)

by_proj=collections.defaultdict(list)
for t in tasks:
    cfs={c.get("name"):c.get("display_value") for c in (t.get("custom_fields") or []) if c.get("name")}
    spn=cfs.get("Sesame Part No") or ""
    if not spn: 
        # try from name
        spn=t.get("name","")
    import re
    m=re.match(r"(\d{4})", spn.strip())
    pfx=m.group(1) if m else "????"
    rec=dict(name=t.get("name",""), due=t.get("due_on"),
             status=cfs.get("Order Status") or cfs.get("Order Status "),
             action=cfs.get("STOCK ACTION"), vendor=cfs.get("Vendor"),
             qty=cfs.get("ORDER QTY"), gid=t.get("gid"))
    by_proj[pfx].append(rec)

for pfx in sorted(by_proj, key=lambda k:-len(by_proj[k])):
    rows=by_proj[pfx]
    dated=[r for r in rows if r["due"]]
    print(f"\n### {pfx}  —  {len(rows)} open parts  ({len(dated)} with due date)")
    # show the dated / ordered ones (most decision-relevant)
    shown=0
    for r in sorted(rows, key=lambda x:(x["due"] or "9999")):
        if not r["due"] and not (r["status"]):
            continue
        print(f"   due {r['due'] or '-'} | {str(r['status'] or '-')[:24]:24} | {str(r['vendor'] or '-')[:16]:16} | {r['name'][:55]}")
        shown+=1
        if shown>=18: 
            rest=len([x for x in rows if x['due'] or x['status']])-18
            if rest>0: print(f"   ... +{rest} more dated/ordered")
            break
