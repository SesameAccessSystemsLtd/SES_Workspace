import httpx, os
pat = None
with open(os.path.expanduser('~/.Hermes/.env'), encoding='utf-8-sig') as f:
    for line in f:
        line = line.strip()
        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN'):
            pat = line.split('=', 1)[1].strip().strip(chr(34)).strip(chr(39))
            break
c = httpx.Client(timeout=30)
H = {'Authorization': 'Bearer ' + pat, 'Accept': 'application/json'}

def search_text(q):
    r = c.get('https://app.asana.com/api/1.0/workspaces/357939803556123/tasks/search',
              headers=H, params={'text': q, 'opt_fields': 'name', 'limit': '100'})
    out = {}
    if r.status_code == 200:
        for it in r.json().get('data', []):
            out[it['gid']] = it.get('name', '')
    return r.status_code, out

for q in ['[PROJECT', 'PROJECT NAME', 'Packing Up the Lift', 'QC Lift']:
    code, res = search_text(q)
    print(f'query={q!r} code={code} n={len(res)}')
    for gid, name in sorted(res.items(), key=lambda kv: kv[1].lower())[:30]:
        print(f'   {gid}: {name!r}')
    print()
