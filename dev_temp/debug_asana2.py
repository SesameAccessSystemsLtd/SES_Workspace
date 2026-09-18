import httpx, os
pat = None
with open(os.path.expanduser('~/.Hermes/.env'), encoding='utf-8-sig') as f:
    for line in f:
        line = line.strip()
        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN='):
            pat = line.split('=', 1)[1].strip().strip('"').strip("'")
            break
print('pat_len=', len(pat), 'first5=', pat[:5])
c = httpx.Client(timeout=30)
H = {'Authorization': 'Bearer ' + pat, 'Accept': 'application/json'}

def try_req(label, method, url, **kw):
    try:
        if method == 'get':
            r = c.get(url, headers=H, **kw)
        else:
            r = c.post(url, headers=H, **kw)
        print(label, r.status_code, r.text[:300])
    except Exception as e:
        print(label, 'EXC', repr(e))

# 1. users/me (should be 200 if token valid)
try_req('users/me', 'get', 'https://app.asana.com/api/1.0/users/me', params={'opt_fields':'email'})

W = 'https://app.asana.com/api/1.0/workspaces/357939803556123/tasks/search'
# 2. POST query in body to search endpoint
try_req('POST body query', 'post', W, params={'opt_pretty':'true'}, json={'query':'PROJECT NAME'})
# 3. GET query as query param
try_req('GET params query', 'get', W, params={'query':'PROJECT NAME'})
# 4. POST without body but query param
try_req('POST params query', 'post', W, params={'query':'PROJECT NAME'})
