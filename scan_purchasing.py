import os, json, time
import requests

TOKEN = os.environ.get('ASANA_PERSONAL_ACCESS_TOKEN')
if not TOKEN:
    raise SystemExit("No ASANA_PERSONAL_ACCESS_TOKEN in env")
BASE = "https://app.asana.com/api/1.0"
H = {'Authorization': f'Bearer {TOKEN}'}
GILES_GID = '1142108324926610'
CUTOFF = '2026-08-17T00:00:00'

# Quote Control project gid - need to resolve. The prompt gives the Quote custom field gid.
# Projects to scan:
PROJECTS = {
    'Parts Control': '1211660707080749',
    'Order Numbers': '854650811917168',
    'Build/Project Orders': '848657415032331',
    'QC issues / Parts Needed / Causing delays': '737971594126804',
    # Quote Control gid need to discover (contains quote custom field 921101053132341)
}
QUOTE_CF = '921101053132341'

def get(url, params=None):
    if params is None:
        params = {}
    params['limit'] = 100
    resp = requests.get(BASE + url, headers=H, params=params)
    if resp.status_code != 200:
        print(f"  ERROR {resp.status_code}: {resp.text[:300]}")
        return None
    return resp.json()

def get_tasks(project_gid):
    """Paginate all tasks in a project. Returns list of {gid,name,modified_at,...}"""
    out = []
    offset = None
    url = f"/projects/{project_gid}/tasks"
    while True:
        params = {'opt_fields': 'name,modified_at,completed,completed_at'}
        if offset:
            params['offset'] = offset
        data = get(url, params)
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
    data = get(f"/tasks/{task_gid}/stories",
               params={'opt_fields': 'created_at,created_by.name,created_by.gid,type,text,resource_subtype'})
    return data.get('data', []) if data else []

def discover_projects():
    """Find projects in user's workspaces matching purchasing project names (incl Quote Control)."""
    found = {}
    # get workspaces
    ws_data = get("/workspaces", params={'opt_fields': 'name'})
    workspaces = ws_data.get('data', []) if ws_data else []
    for ws in workspaces:
        url = f"/projects"
        offset = None
        while True:
            params = {'workspace': ws['gid'], 'opt_fields': 'name', 'limit': 100}
            if offset:
                params['offset'] = offset
            data = get(url, params)
            if not data:
                break
            for p in data.get('data', []):
                found[p.get('name')] = p['gid']
            np = data.get('next_page')
            if np and np.get('offset'):
                offset = np['offset']
            else:
                break
    return found

# Optional: find quote project via custom field fetch
def find_quote_project():
    data = get(f"/custom_fields/{QUOTE_CF}", params={'opt_fields': 'name'})
    if data:
        print("Quote custom field:", data.get('data', {}).get('name'))

# Find all projects to confirm names & quote control project gid
all_projects = discover_projects()
print("=== Projects found (matching names) ===")
for name in sorted(all_projects):
    low = name.lower()
    if any(k in low for k in ['quote', 'parts control', 'order', 'qc issue', 'build/project', 'parts needed']):
        print(f"  {name}: {all_projects[name]}")
