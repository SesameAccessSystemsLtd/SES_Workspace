import os, json, urllib.request, re
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
r=urllib.request.Request(f'https://app.asana.com/api/1.0/projects/{BUILD}/tasks?opt_fields=name,completed&limit=100',headers=h)
d=json.load(urllib.request.urlopen(r,timeout=60)); tasks=d['data']; nxt=d.get('next_page')
while nxt:
    r=urllib.request.Request(nxt['uri'],headers=h); dd=json.load(urllib.request.urlopen(r,timeout=60)); tasks+=dd['data']; nxt=dd.get('next_page')
prefixes=("laser cut","machining","powder coating","engraving","anodising","finish","nbss","sesame","marine lacquer","cement board","glass","bellows")
print("Open vendor-group-prefix-matching tasks for the 5 jobs that DO appear:")
for job in ['1373','1535','1569','1582','1597']:
    print(f"  job {job}:")
    for t in tasks:
        nm=t.get('name') or ''
        if t['completed']: continue
        if re.search(rf'\b{job}\b',nm) and any(nm.strip().lower().startswith(p) for p in prefixes) and re.search(r'\d{3,4}',nm):
            print(f"     {nm[:70]}")
