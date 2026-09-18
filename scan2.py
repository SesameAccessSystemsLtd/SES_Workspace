import os, json
import requests

TOKEN = os.environ.get('ASANA_PERSONAL_ACCESS_TOKEN')
if not TOKEN:
    raise SystemExit("No ASANA_PERSONAL_ACCESS_TOKEN in env")
BASE = "https://app.asana.com/api/1.0"
H = {'Authorization': 'Bearer ' + TOKEN}
GILES_GID = '1142108324926610'
CUTOFF = '2026-08-17T00:00:00'

PROJECTS = {
    'Parts Control': '1211660707080749',
    'Quote Control': '1207766004159548',
    'Order Numbers': '854650811917168',
    'Build/Project Orders': '848657415032331',
    'QC issues / Parts Needed / Causing delays': '737971594126804',
}

def api(url, params=None):
    if params is None:
        params = {}
    params['limit'] = 100
    resp = requests.get(BASE + url, headers=H, params=params)
    if resp.status_code != 200:
        print("  ERROR %s %s: %s" % (resp.status_code, url, resp.text[:200]))
        return None
    return resp.json()

def get_tasks(project_gid):
    out = []
    offset = None
    while True:
        params = {'opt_fields': 'name,modified_at,completed,completed_at', 'limit': 100}
        if offset:
            params['offset'] = offset
        data = api("/projects/%s/tasks" % project_gid, params)
        if not data:
            break
        out.extend(data.get('data', []))
        np = data.get('next_page')
        if np and np.get('offset'):
            offset = np['offset']
        else:
            break
    return out

def get_stories(task_gid):
    data = api("/tasks/%s/stories" % task_gid,
               params={'opt_fields': 'created_at,created_by.name,created_by.gid,type,text,resource_subtype'})
    return data.get('data', []) if data else []

def is_recent(modified_at):
    return bool(modified_at) and modified_at > CUTOFF

def is_giles(story):
    cb = story.get('created_by') or {}
    name = str(cb.get('name') or '')
    gid = str(cb.get('gid') or '')
    return ('giles' in name.lower()) or (gid == GILES_GID)

PROC_KEYWORDS = [
    'wrong vendor', 'wrong supplier', 'fallback', 'bom supplier',
    'not matched', 'did not match', 'route', 'routed',
    'supplier', 'procurement', 'process', 'procedure', 'should not',
    'keeps happening', 'still happening', 'needs to be fixed', 'needs fixing',
    'duplicate', 'already', 'blank vendor', 'vendor blank', 'vendor is blank',
    'split', 'incorrect', 'instead of', 'mismatch',
    'should be', 'should have', 'must ', 'why is ', 'why are ', 'why does ', 'why do ',
    'this is happening', 'no vendor', 'cannot find', 'fall back',
    'automatically', 'lower case', 'uppercase', 'closed prematurely',
    'moved to', 'should route', 'goes to', 'went to', 'issue', 'problem', 'qc proc',
    'does', 'has been', 'was completed', 'not the correct', 'amended', 'correction',
    'check', 'needs to go', 'logged', 're-added', 'does not match', "doesn't match",
    'keeps', 'another', 'yet again', 'again',
]

def looks_procedural(text):
    lt = text.lower()
    hits = [k for k in PROC_KEYWORDS if k in lt]
    return len(hits) >= 1, hits

def scan():
    report = []
    all_recent = 0
    for pname, pgid in PROJECTS.items():
        print("")
        print("=== SCANNING %s (%s) ===" % (pname, pgid))
        tasks = get_tasks(pgid)
        print("  total tasks fetched: %d" % len(tasks))
        recent = [t for t in tasks if is_recent(t.get('modified_at'))]
        all_recent += len(recent)
        print("  recently modified: %d" % len(recent))
        for t in recent:
            tgid = t['gid']
            tname = t.get('name', '') or ''
            stories = get_stories(tgid)
            comments = [s for s in stories
                        if s.get('type') == 'comment'
                        or s.get('resource_subtype') in ('comment_added', 'comment')]
            for gc in comments:
                if not is_giles(gc):
                    continue
                text = gc.get('text', '') or ''
                if not text:
                    continue
                ok, hits = looks_procedural(text)
                if ok:
                    report.append({
                        'project': pname,
                        'project_gid': pgid,
                        'task_gid': tgid,
                        'task_name': tname,
                        'comment_date': gc.get('created_at', ''),
                        'author': (gc.get('created_by') or {}).get('name', ''),
                        'comment': text,
                        'task_modified': t.get('modified_at', ''),
                        'hits': hits,
                    })
    print("\n\n===== RESULTS: total recent tasks=%d, candidate records=%d =====" % (all_recent, len(report)))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'giles_results.json'), 'w') as f:
        json.dump(report, f, indent=2)
    for r in report:
        print("-----")
        print("[%s] %s" % (r['project'], r['task_name']))
        print("  project=%s task=%s task_modified=%s" % (r['project_gid'], r['task_gid'], r['task_modified']))
        print("  comment by %s on %s  hits=%s" % (r['author'], r['comment_date'], r['hits']))
        print("  COMMENT: %s" % r['comment'])

scan()
