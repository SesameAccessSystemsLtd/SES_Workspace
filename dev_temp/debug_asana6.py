import httpx, os, time
pat = None
with open(os.path.expanduser('~/.Hermes/.env'), encoding='utf-8-sig') as f:
    for line in f:
        line = line.strip()
        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN'):
            pat = line.split('=', 1)[1].strip().strip(chr(34)).strip(chr(39))
            break
c = httpx.Client(timeout=30)
H = {'Authorization': 'Bearer ' + pat, 'Accept': 'application/json'}
SLEEP = 0.5

def get(url, params=None):
    return c.get(url, headers=H, params=params or {})

def get_all(url, params=None):
    out = {}
    offset = None
    params = dict(params or {})
    while True:
        p = dict(params); p['limit'] = '100'; p['opt_fields'] = 'name'
        if offset: p['offset'] = offset
        r = get(url, p)
        if r.status_code != 200:
            print('ERR', url, r.status_code, r.text[:150]); return out
        for it in r.json().get('data', []):
            out[it['gid']] = it.get('name', '')
        np = r.json().get('next_page') or {}
        offset = np.get('offset')
        if not offset: break
        time.sleep(SLEEP)
    return out

proj = get_all('https://app.asana.com/api/1.0/projects/1216293165459681/tasks')
print('PROJECT TASKS:', len(proj))
for gid, name in sorted(proj.items(), key=lambda kv: kv[1].lower()):
    print('  ', gid, repr(name))

# Crawl subtrees
all_tasks = dict(proj)
frontier = list(proj.keys())
visited = set()
while frontier:
    tgid = frontier.pop()
    if tgid in visited: continue
    visited.add(tgid)
    subs = get_all('https://app.asana.com/api/1.0/tasks/' + tgid + '/subtasks')
    for sgid, sname in subs.items():
        if sgid not in all_tasks:
            all_tasks[sgid] = sname
            frontier.append(sgid)
    time.sleep(SLEEP)

print('SUBTREE TOTAL:', len(all_tasks))
print()
print('Tasks with placeholder-like names:')
import re
rx = re.compile(r'\[\s*project\s*name\s*\]', re.IGNORECASE)
for gid, name in sorted(all_tasks.items(), key=lambda kv: kv[1].lower()):
    if rx.search(name or ''):
        print('  PLACEHOLDER', gid, repr(name))
