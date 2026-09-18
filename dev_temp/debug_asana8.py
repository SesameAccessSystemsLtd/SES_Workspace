import httpx, os, re
pat = None
with open(os.path.expanduser('~/.Hermes/.env'), encoding='utf-8-sig') as f:
    for line in f:
        line = line.strip()
        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN'):
            pat = line.split('=', 1)[1].strip().strip(chr(34)).strip(chr(39))
            break
c = httpx.Client(timeout=30)
H = {'Authorization': 'Bearer ' + pat, 'Accept': 'application/json'}
rx = re.compile(r'\[\s*project\s*name\s*\](?!\s*\]).*', re.IGNORECASE)
# simpler regex: placeholder bracket block
simple = re.compile(r'\[\s*project\s*name\s*\]', re.IGNORECASE)

for q in ['PROJECT NAME', '[PROJECT']:
    total = 0
    matches = {}
    offset = None
    page = 0
    while True:
        page += 1
        p = {'text': q, 'opt_fields': 'name', 'limit': '100'}
        if offset: p['offset'] = offset
        r = c.get('https://app.asana.com/api/1.0/workspaces/357939803556123/tasks/search', headers=H, params=p)
        if r.status_code != 200:
            print(q, 'ERR page', page, r.status_code, r.text[:120]); break
        d = r.json().get('data', [])
        total += len(d)
        for it in d:
            nm = it.get('name', '')
            if simple.search(nm or ''):
                matches[it['gid']] = nm
        np = r.json().get('next_page') or {}
        offset = np.get('offset')
        print(f'  {q} page {page}: recv={len(d)} total={total} regex_matches={len(matches)}')
        if not offset or page >= 100:
            break
    print(f'== {q}: TOTAL_RECV={total} REGEX_MATCHES={len(matches)}')
    for gid, nm in sorted(matches.items(), key=lambda kv: kv[1].lower()):
        print(f'   MATCH {gid}: {nm!r}')
