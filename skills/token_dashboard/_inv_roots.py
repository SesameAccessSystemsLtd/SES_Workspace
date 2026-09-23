import os, json, urllib.request, time

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

BUILD_ORDERS='848657415032331'  # Build/Project Orders
print("=== Search ALL BOMS roots for 1580 in Build/Project Orders ===")
# paginate tasks in project, filter name contains 'ALL BOMS' and '1580'
r=urllib.request.Request(f'https://app.asana.com/api/1.0/projects/{BUILD_ORDERS}/tasks?opt_fields=name,completed,created_at,completed_at,due_on&limit=100',headers=h)
data=json.load(urllib.request.urlopen(r,timeout=60))
tasks=data.get('data',[])
nxt=data.get('next_page')
while nxt:
    r=urllib.request.Request(nxt['uri'],headers=h)
    d=json.load(urllib.request.urlopen(r,timeout=60)); tasks+=d.get('data',[]); nxt=d.get('next_page')
print(f"total tasks in Build/Project Orders: {len(tasks)}")
for t in tasks:
    nm=t.get('name') or ''
    if '1580' in nm and ('BOM' in nm.upper() or 'ALL BOMS' in nm.upper()):
        print(f"  [{'x' if t['completed'] else ' '}] {nm[:70]:70s} created={t.get('created_at','')[:10]} compl={str(t.get('completed_at'))[:10]}")
print()
print("=== ALL tasks anywhere with 1580 in Build/Project Orders (name sample) ===")
seen=0
for t in tasks:
    nm=t.get('name') or ''
    if '1580' in nm:
        seen+=1
        if seen<=40:
            print(f"  [{'x' if t['completed'] else ' '}] {nm[:75]}")
print(f"  ...total 1580-named: {seen}")
