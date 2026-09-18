import httpx, os, re, time
pat = None
with open(os.path.expanduser('~/.Hermes/.env'), encoding='utf-8-sig') as f:
    for line in f:
        line = line.strip()
        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN'):
            pat = line.split('=', 1)[1].strip().strip(chr(34)).strip(chr(39))
            break
c = httpx.Client(timeout=30)
H = {'Authorization': 'Bearer ' + pat, 'Accept': 'application/json'}
rx = re.compile(r'\[\s*project\s*name\s*\]', re.IGNORECASE)
def get_all(url):
    out={}; offset=None
    while True:
        p={'limit':'100','opt_fields':'name'}
        if offset: p['offset']=offset
        r=c.get(url,headers=H,params=p)
        if r.status_code!=200:
            print('ERR',r.status_code,r.text[:120]); break
        for it in r.json().get('data',[]): out[it['gid']]=it.get('name','')
        np=r.json().get('next_page') or {}; offset=np.get('offset')
        if not offset: break
        time.sleep(0.3)
    return out
remaining={}
_crawled=set()
def crawl(gid):
    if gid in _crawled: return
    _crawled.add(gid)
    for sgid,sname in get_all('https://app.asana.com/api/1.0/tasks/'+gid+'/subtasks').items():
        if rx.search(sname or ''): remaining[sgid]=sname
        crawl(sgid)
for gid,name in get_all('https://app.asana.com/api/1.0/projects/1216293165459681/tasks').items():
    if rx.search(name or ''): remaining[gid]=name
    crawl(gid)
print('VERIFY: project-scope placeholder tasks remaining =', len(remaining))
for gid,nm in sorted(remaining.items(), key=lambda kv: kv[1].lower()):
    print('  REMAIN', gid, repr(nm))
