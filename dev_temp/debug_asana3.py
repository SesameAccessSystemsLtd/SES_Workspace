import httpx, os
pat = None
with open(os.path.expanduser('~/.Hermes/.env'), encoding='utf-8-sig') as f:
    for line in f:
        line = line.strip()
        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN=***            pat = line.split('=', 1)[1].strip().strip('"').strip("'")
            break
c = httpx.Client(timeout=30)
H = {'Authorization': 'Bearer ' + pat, 'Accept': 'application/json'}

def try_req(label, method, url, **kw):
    try:
        if method == 'get':
            r = c.get(url, headers=H, **kw)
        else:
            r = c.post(url, headers=H, **kw)
        print(label, r.status_code, r.text[:350])
    except Exception as e:
        print(label, 'EXC', repr(e))

W = 'https://app.asana.com/api/1.0/workspaces/357939803556123/tasks/search'
P = 'https://app.asana.com/api/1.0/projects/1216293165459681/tasks/search'

# GET workspace search with text query params
try_req('GET ws query', 'get', W, params={'query': 'PROJECT NAME', 'opt_fields':'name'})
try_req('GET ws query contains', 'get', W, params={'text':'PROJECT NAME'})
# POST project tasks/search
try_req('POST proj search', 'post', P, json={'query':'PROJECT NAME', 'opt_fields':'name'})
try_req('POST proj search params', 'post', P, params={'query':'PROJECT NAME'})
