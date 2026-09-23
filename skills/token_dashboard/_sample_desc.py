import sqlite3, os, json, urllib.request
DB = r"file:C:/Users/GilesHugo/SES_Workspace/db/ops.db?mode=ro"
c = sqlite3.connect(DB, uri=True); c.row_factory = sqlite3.Row
env={}
for line in open(r'C:\Users\GilesHugo\.hermes\.env'):
    line=line.strip()
    if '=' in line and not line.startswith('#'):
        k,v=line.split('=',1); env[k]=v.strip().strip('"').strip("'")
tok=env.get('ASANA_PERSONAL_ACCESS_TOKEN') or env.get('ASANA_PAT')
h={'Authorization':'Bearer '+tok}
def get(path):
    r=urllib.request.Request('https://app.asana.com/api/1.0'+path,headers=h)
    return json.load(urllib.request.urlopen(r,timeout=30))['data']

# The manual example
print("=== 1591.7003.G.013 (manual example) ===")
t=get('/tasks/1217546564991330?opt_fields=name,notes')
n=t.get('notes') or ''
print("  has 'Assembly Pack' line:", "Assembly Pack" in n)
for ln in n.splitlines():
    if 'Assembly Pack' in ln: print("   ->", ln)

# Broader: parts with a resolved gid but NO ref_name (manual links w/o the field synced?)
print("\n=== linked parts with NO build_pack_ref_name (can't write a line) ===")
n2=c.execute("""SELECT COUNT(*) FROM parts_control
   WHERE build_assembly_pack_gid IS NOT NULL AND build_assembly_pack_gid<>''
     AND (build_pack_ref_name IS NULL OR build_pack_ref_name='')""").fetchone()[0]
print("  count:", n2)
for r in c.execute("""SELECT sesame_pn, build_assembly_pack_gid FROM parts_control
   WHERE build_assembly_pack_gid IS NOT NULL AND build_assembly_pack_gid<>''
     AND (build_pack_ref_name IS NULL OR build_pack_ref_name='') LIMIT 10"""):
    print("   ", r["sesame_pn"], "gid=", r["build_assembly_pack_gid"])
