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
known = {'1216293066712438', '1216293066712442', '1216293054994605', '1216293066510705'}

def search_one(q, maxpages=10):
    offset=None; total=0; found_known=[]
    for page in range(1, maxpages+1):
        p = {'text': q, 'opt_fields': 'name', 'limit': '100'}
        if offset: p['offset']=offset
        r = c.get('https://app.asana.com/api/1.0/workspaces/357939803556123/tasks/search', headers=H, params=p)
        if r.status_code != 200:
            print('  ERR', r.status_code, r.text[:120]); break
        d=r.json().get('data',[])
        total+=len(d)
        for it in d:
            if it['gid'] in known:
                found_known.append((it['gid'], it.get('name','')))
        np=r.json().get('next_page') or {}
        offset=np.get('offset')
        if not offset or page>=maxpages:
            break
        time.sleep(0.2)
    print(f'  text={q!r}: recv={total} known_hit={found_known}')
    return total

for q in ['[Project Name]', 'QC Lift -', 'Packing Up the Lift -', '[project', 'ARCHIVE - [Project', 'Project Name QUALITY CONTROL']:
    search_one(q, maxpages=3)
