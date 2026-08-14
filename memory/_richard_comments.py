import os, requests, json, time

env = 'C:/Users/GilesHugo/AppData/Local/hermes/.env'
tok = None
with open(env) as f:
    for line in f:
        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN'):
            tok = line.split('=', 1)[1].strip().strip('"').strip("'")
            break
h = {'Authorization': f'Bearer {tok}'}

results = json.load(open('memory/richard_tag_tasks.json'))

def get_comments(task_gid):
    url = f'https://app.asana.com/api/1.0/tasks/{task_gid}/stories'
    out, offset = [], None
    while True:
        q = {'opt_fields': 'text,created_at,created_by.name,type,resource_subtype', 'limit': 100}
        if offset:
            q['offset'] = offset
        j = requests.get(url, headers=h, params=q).json()
        if 'data' not in j:
            break
        out += j['data']
        np = j.get('next_page') or {}
        offset = np.get('offset')
        if not offset or not np:
            break
    return out

all_rows = []
seen = set()
for tag_name, tasks in results.items():
    for t in tasks:
        gid = t['gid']
        if gid in seen:
            continue
        seen.add(gid)
        try:
            stories = get_comments(gid)
        except Exception as e:
            stories = []
            print(f'ERR {gid}: {e}', flush=True)
        comments = [s for s in stories if s.get('type') == 'comment' and s.get('text', '').strip()]
        if not comments:
            continue
        for c in comments:
            all_rows.append({
                'project': tag_name,
                'task_gid': gid,
                'task_name': t.get('name', ''),
                'task_completed': t.get('completed'),
                'comment_by': (c.get('created_by') or {}).get('name'),
                'created_at': c.get('created_at'),
                'text': c.get('text', '')
            })
        time.sleep(0.12)
        if len(seen) % 60 == 0:
            print(f'  processed {len(seen)}/{len(sum([v for v in results.values()], []))} tasks, {len(all_rows)} comments', flush=True)

json.dump(all_rows, open('memory/richard_proc_issue_comments.json', 'w'), indent=2)
print(f'TOTAL comment rows: {len(all_rows)} across {len(seen)} tasks')
