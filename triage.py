import json, sys
d = json.load(open('giles_results.json'))
# sort: by project then date to keep ordering predictable
key = lambda r: (list(dict.fromkeys(x['project'] for x in d)).index(r['project']) if False else r['project'], r['comment_date'])
byp = {}
order = ['Parts Control','Quote Control','Order Numbers','Build/Project Orders','QC issues / Parts Needed / Causing delays']
for p in order:
    byp[p] = [r for r in d if r['project']==p]
idx = 0
out = []
for p in order:
    for r in byp[p]:
        idx += 1
        out.append("%d|%s|%s|%s|%s|%s|%s|%s" % (
            idx, r['project'], r['task_name'].replace('\t',' ')[:60],
            r['project_gid'], r['task_gid'], r['comment_date'][:10],
            (r['author'] or ''), r['comment'].replace('\n',' ')[:400]))
open('triage.txt','w',encoding='utf-8').write('\n'.join(out))
print('\n'.join(out))
