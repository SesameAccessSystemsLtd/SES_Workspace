#!/usr/bin/env python3
"""Combine workspace-search + full subtree crawl; update all placeholder tasks; verify."""
import httpx, os, re, time, sys, json
REPLACEMENT = "1591 - University of Cambridge"
PLACEHOLDER_RE = re.compile(r"\[\s*project\s*name\s*\]", re.IGNORECASE)
PROJECT_GID = "1216293165459681"
WORKSPACE_GID = "357939803556123"
SLEEP = 0.4

# --- PAT ---
pat = None
with open(os.path.expanduser('~/.Hermes/.env'), encoding='utf-8-sig') as f:
    for line in f:
        line = line.strip()
        if line.startswith('ASANA_PERSONAL_ACCESS_TOKEN'):
            pat = line.split('=', 1)[1].strip().strip(chr(34)).strip(chr(39))
            break
assert pat, "PAT not found"
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
            print('ERR', r.status_code, r.text[:150], flush=True); break
        for it in r.json().get('data', []):
            out[it['gid']] = it.get('name', '')
        np = r.json().get('next_page') or {}
        offset = np.get('offset')
        if not offset: break
        time.sleep(SLEEP)
    return out

def put_name(gid, name):
    for _ in range(5):
        r = c.put('https://app.asana.com/api/1.0/tasks/' + gid,
                  headers=H, json={'data': {'name': name}})
        if r.status_code in (200, 201):
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2); continue
        print('PUT ERR', r.status_code, r.text[:200], flush=True)
        return None
    return None

# ---- 1) Collect tasks via workspace text-search (safety net) ----
search_pool = {}
try:
    for q in ['QC Lift -', 'Packing Up the Lift -', 'ARCHIVE - [Project', '[Project Name]', 'PROJECT NAME']:
        offset = None
        for _page in range(3):
            p = {'text': q, 'opt_fields': 'name', 'limit': '100'}
            if offset: p['offset'] = offset
            r = c.get('https://app.asana.com/api/1.0/workspaces/%s/tasks/search' % WORKSPACE_GID,
                      headers=H, params=p)
            if r.status_code != 200:
                break
            for it in r.json().get('data', []):
                search_pool[it['gid']] = it.get('name', '')
            np = r.json().get('next_page') or {}
            offset = np.get('offset')
            if not offset: break
            time.sleep(SLEEP)
    print('workspace-search pool tasks:', len(search_pool), flush=True)
except Exception as e:
    print('search skipped:', repr(e), flush=True)

# ---- 2) Full subtree crawl of the project ----
all_tasks = dict(search_pool)  # seed with search results (dedupe by gid)
project_scope = {}            # definitive set: project tasks + descendants
_crawled = set()
def crawl_subtrees(root_gid, root_name, is_project_top=True):
    if root_gid in _crawled:
        return
    _crawled.add(root_gid)
    subs = get_all('https://app.asana.com/api/1.0/tasks/' + root_gid + '/subtasks')
    for sgid, sname in subs.items():
        if sgid not in project_scope:
            project_scope[sgid] = sname
        # even if already seen, still recurse to ensure full descendant coverage
        project_scope.setdefault(sgid, sname)
        crawl_subtrees(sgid, sname, False)
        all_tasks.setdefault(sgid, sname)

for gid, name in get_all('https://app.asana.com/api/1.0/projects/%s/tasks' % PROJECT_GID).items():
    project_scope.setdefault(gid, name)
    all_tasks.setdefault(gid, name)
    crawl_subtrees(gid, name)
print('project_subtree task count:', len(project_scope), flush=True)

# ---- 3) Filter by placeholder regex (case-insensitive) ----
matches = {}
for gid, name in all_tasks.items():
    if PLACEHOLDER_RE.search(name or ''):
        matches[gid] = name
print('ALL placeholder matches (workspace incl.):', len(matches), flush=True)
for gid, name in sorted(matches.items(), key=lambda kv: kv[1].lower()):
    in_scope = gid in project_scope
    print('  %s %s %r' % ('PROJ' if in_scope else 'X', gid, name), flush=True)

# Restrict updates to the project scope (definitive). Report out-of-scope separately.
to_update = {g: n for g, n in matches.items() if g in project_scope}
out_of_scope = {g: n for g, n in matches.items() if g not in project_scope}
print('in-project matches to update:', len(to_update), flush=True)
print('out-of-project matches (reported, not updated):', len(out_of_scope), flush=True)

# ---- 4) Update ----
updated = []
for gid, old_name in sorted(to_update.items(), key=lambda kv: kv[1].lower()):
    new_name = PLACEHOLDER_RE.sub(REPLACEMENT, old_name)
    if new_name == old_name:
        print('  SKIP (no change)', gid, flush=True)
        continue
    res = put_name(gid, new_name)
    if res is not None:
        updated.append({'gid': gid, 'old': old_name, 'new': new_name})
        print('  UPDATED %s' % gid, flush=True)
        print('    old: %r' % old_name, flush=True)
        print('    new: %r' % new_name, flush=True)
    else:
        print('  FAILED %s' % gid, flush=True)
    time.sleep(SLEEP)

print('UPDATED COUNT:', len(updated), flush=True)

result = {
    'updated': updated,
    'out_of_scope': [{'gid': g, 'name': n} for g, n in out_of_scope.items()],
}
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'asana_update_result.json'),
          'w', encoding='utf-8') as f:
    json.dump(result, f, indent=2, ensure_ascii=False)
print('result written', flush=True)
