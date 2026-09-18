import json
d = json.load(open('giles_results.json'))
# exclude automated service account (not the human Giles)
d = [r for r in d if 'service account' not in (r['author'] or '').lower()]
order = ['Parts Control','Quote Control','Order Numbers','Build/Project Orders','QC issues / Parts Needed / Causing delays']
idx = 0
lines = []
for p in order:
    lines.append("############ PROJECT: %s ############" % p)
    rows = [r for r in d if r['project']==p]
    rows.sort(key=lambda r: r['comment_date'])
    for r in rows:
        idx += 1
        lines.append("")
        lines.append("#%d [%s] task_name: %s" % (idx, r['comment_date'][:10], r['task_name']))
        lines.append("#%d project_gid=%s task_gid=%s task_mod=%s author=%s" % (
            idx, r['project_gid'], r['task_gid'], r['task_modified'][:10], r['author']))
        lines.append("#%d hits=%s" % (idx, r['hits']))
        lines.append("#%d COMMENT: %s" % (idx, r['comment'].replace('\r','')))
        lines.append("-"*90)
open('triage_human.txt','w',encoding='utf-8').write('\n'.join(lines))
print("Wrote triage_human.txt, human records:", idx)
