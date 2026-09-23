import os, json, urllib.request

env={}
for line in open(r'C:\Users\GilesHugo\.hermes\.env'):
    line=line.strip()
    if '=' in line and not line.startswith('#'):
        k,v=line.split('=',1); env[k]=v.strip().strip('"').strip("'")
tok=env.get('ASANA_PERSONAL_ACCESS_TOKEN') or env.get('ASANA_PAT')
h={'Authorization':'Bearer '+tok}
def get(path):
    r=urllib.request.Request('https://app.asana.com/api/1.0'+path,headers=h)
    return json.load(urllib.request.urlopen(r,timeout=30))['data']

BUILD='848657415032331'
# Fetch all open tasks in Build/Project Orders with 1580 in name
r=urllib.request.Request(f'https://app.asana.com/api/1.0/projects/{BUILD}/tasks?opt_fields=name,completed,gid&limit=100',headers=h)
data=json.load(urllib.request.urlopen(r,timeout=60)); tasks=data['data']; nxt=data.get('next_page')
while nxt:
    r=urllib.request.Request(nxt['uri'],headers=h); d=json.load(urllib.request.urlopen(r,timeout=60)); tasks+=d['data']; nxt=d.get('next_page')

prefixes=("laser cut","machining","powder coating","engraving","anodising","finish","nbss","sesame","marine lacquer","cement board","glass","bellows")
print("OPEN 1580 vendor-group candidates (name has 1580), and whether the tracker's prefix test would catch them:")
caught=missed=0
for t in tasks:
    nm=t.get('name') or ''
    if '1580' not in nm: continue
    if t['completed']: continue
    low=nm.strip().lower()
    ok = any(low.startswith(p) for p in prefixes)
    # only consider ones that look like vendor groups (have a job number)
    import re
    if not re.search(r'\d{3,4}',nm): continue
    tag = "CAUGHT" if ok else "MISSED"
    if ok: caught+=1
    else: missed+=1
    print(f"  [{tag}] {nm[:72]}")
print(f"\n  caught={caught} missed={missed}")
