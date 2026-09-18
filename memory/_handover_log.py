import sqlite3
con = sqlite3.connect('db/ops.db')
cur = con.cursor()
print('=== all legacy_bom_handover_log rows ===')
try:
    for r in cur.execute("SELECT task_gid, project_token, status, dropbox_url, resolved_folder, prepared_at, note FROM legacy_bom_handover_log"):
        print('task_gid:', r[0], '| token:', r[1], '| status:', r[2])
        print('   dropbox_url:', r[3])
        print('   resolved_folder:', r[4], '| prepared:', r[5], '| note:', (r[6] or '')[:60])
except Exception as e:
    print('ERR', e)
