import os, requests, json, time

env = 'C:/Users/GilesHugo/AppData/Local/hermes/.env'
tok = None
with open(env) as f:
    for line in f:
        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN'):
            tok = line.split('=', 1)[1].strip().strip('"').strip("'")
            break
h = {'Authorization': f'Bearer {tok}'}

RICHARD = '357614200440807'
TAGS = {'1163007497828518': 'Purchasing Procedure Issue', '1164351154905410': 'Process Issue'}

def paginate(url, fields, extra=None):
    out, offset = [], None
    while True:
        q = {'opt_fields': fields, 'limit': 100}
        if offset:
            q['offset'] = offset
        if extra:
            q.update(extra)
        j = requests.get(url, headers=h, params=q).json()
        if 'data' not in j:
            break
        out += j['data']
        np = j.get('next_page') or {}
        offset = np.get('offset')
        if not offset or not np:
            break
    return out

results = {}
for tag_gid, tag_name in TAGS.items():
    tasks = paginate(f'https://app.asana.com/api/1.0/tags/{tag_gid}/tasks',
                     'name,completed,assignee.name,memberships.section.name')
    # Richard-assigned only
    rich = [t for t in tasks if (t.get('assignee') or {}).get('gid') == RICHARD]
    results[tag_name] = rich
    print(f"[{tag_name}] {len(tasks)} total, {len(rich)} assigned to Richard")

json.dump(results, open('memory/richard_tag_tasks.json', 'w'))
print('saved to memory/richard_tag_tasks.json')
