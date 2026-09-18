import httpx, os, time, re, sys
pat = None
with open(os.path.expanduser('~/.Hermes/.env'), encoding='utf-8-sig') as f:
    for line in f:
        line = line.strip()
        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN'):
            pat = line.split('=', 1)[1].strip().strip(chr(34)).strip(chr(39))
            break
c = httpx.Client(timeout=30)
H = {'Authorization': 'Bearer ' + pat, 'Accept': 'application/json'}

def get_all(url):
    out = {}
    offset = None
    while True:
        p = {'limit': '100', 'opt_fields': 'name'}
        if offset: p['offset'] = offset
        r = c.get(url, headers=H, params=p)
        if r.status_code != 200:
            print('ERR', url, r.status_code, r.text[:150]); return out
        for it in r.json().get('data', []):
            out[it['gid']] = it.get('name', '')
        np = r.json().get('next_page') or {}
        offset = np.get('offset')
        if not offset: break
    return out

rx = re.compile(r'\[\s*project\s*name\s*\]', re.IGNORECASE)
placeholders = {}
def crawl(tgid, depth=0):
    subs = get_all('https://app.asana.com/api/1.0/tasks/' + tgid + '/subtasks')
    for sgid, sname in subs.items():
        if rx.search(sname or ''):
            placeholders[sgid] = sname
            print('PLACEHOLDER', sgid, repr(sname), flush=True)
        crawl(sgid, depth+1)

count = 0
for gid, name in get_all('https://app.asana.com/api/1.0/projects/1216293165459681/tasks').items():
    count += 1
    if rx.search(name or ''):
        placeholders[gid] = name
        print('PLACEHOLDER', gid, repr(name), flush=True)
    crawl(gid)
print('processed top-level:', count, flush=True)
print('TOTAL PLACEHOLDERS FOUND:', len(placeholders), flush=True)
