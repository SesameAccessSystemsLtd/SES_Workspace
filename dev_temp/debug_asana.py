import httpx, os
pat = None
with open(os.path.expanduser('~/.Hermes/.env'), encoding='utf-8-sig') as f:
    for line in f:
        line = line.strip()
        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN'):
            pat = line.split('=', 1)[1].strip()
            break
print('pat_len=', len(pat))
print('first5=', pat[:5])
c = httpx.Client(timeout=30)
for url, params, method in [
    ('https://app.asana.com/api/1.0/users/me', {'opt_fields': 'name,email'}, 'get'),
]:
    h = {'Authorization': 'Bearer ' + pat, 'Accept': 'application/json'}
    if method == 'get':
        r = c.get(url, params=params, headers=h)
    print(url, r.status_code, r.text[:200])
