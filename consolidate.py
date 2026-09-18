import json
d = json.load(open('giles_results.json'))
# exclude service account
d = [r for r in d if 'service account' not in (r['author'] or '').lower()]

# Task gids I've triaged as genuine purchasing procedure/process issues
# (map task_gid -> best project label; project preference order)
pref = ['Parts Control','Quote Control','Order Numbers','Build/Project Orders','QC issues / Parts Needed / Causing delays']
qual = {
 '1214803030808195':'Leg Cap Machining process issues (duplicate part-no round vs square; status not updated; inhouse stock parts pulled)',
 '1211427422578875':'Duplicate engraved-plate task - one shown already in parent task',
 '1211159750454174':'STOP assigning parts to approvals role - removes from stock & triggers replacement order with no PO',
 '1211100300359102':'Legacy BOM dumped in BOM folder -> 79 duplicate tasks imported; changed format breaks scripts; BOM placed outside agreed task',
 '1214635677104888':'Blocked/on-hold dependent parts not assigned after approval sign-off - big issue, wants auto-assign + unblock date',
 '1216242331971326':'Workflow/process issue - meeting outcomes not picked up, unsure correct high-priority workflow for parts',
 '1216623913809597':'Item naming inconsistency across orders makes previous orders/parts unfindable (sex bolt vs barrel nut)',
 '1216239283983165':'Invoice on wrong PO / logged against wrong order + process question for correct parts-against-order logging',
 '1216755221876955':'Order earlier not processed/flagged - being processed now',
 '1216611337149396':'Picking task not picked or flagged to order - missed flag for purchasing',
 '1217014939428772':'Order processed under existing/unrelated PO instead of correct task',
 '1213212124682884':'Parts taken to anodiser with no trace/record of what went & condition',
 '1216622543618516':'Order progressed with 3 sheets instead of the amended 1 sheet 18mm (wrong qty on order)',
 '1216850437678797':'Goods received not booked-in/QC checked - ensure anything arrived is booked in then QC checked',
 '1216623812073748':'QC-fail lasercut part progressed into production / kitted - plus future dp1-on-4mm brush process note',
}

rows = []
for r in d:
    g = r['task_gid']
    if g in qual:
        rows.append(r)
# consolidate comments by (task_gid): gather all Giles procedural comments
from collections import OrderedDict
by_task = OrderedDict()
for r in rows:
    by_task.setdefault(r['task_gid'], []).append(r)

print("CONSOLIDATED qualifying tasks:", len(by_task))
lines = []
for g, recs in by_task.items():
    task_name = recs[0]['task_name']
    # pick primary project by preference order (comment may appear under several)
    proj = None
    for p in pref:
        if any(r['project']==p for r in recs):
            proj = p; break
    pgid = next(r['project_gid'] for r in recs if r['project']==proj)
    task_mod = recs[0]['task_modified']
    note = qual[g]
    lines.append("="*100)
    lines.append("TASK_NAME: %s" % task_name)
    lines.append("TASK %s  PROJECT %s(%s)  task_modified=%s" % (g, proj, pgid, task_mod[:10]))
    lines.append("ISSUE_NOTE: %s" % note)
    # dedupe comments by (date,text)
    seen = set()
    for r in sorted(recs, key=lambda x:x['comment_date']):
        key = (r['comment_date'], r['comment'])
        if key in seen: continue
        seen.add(key)
        lines.append("  [Giles comment %s by %s]: %s" % (r['comment_date'][:10], r['author'], r['comment'].replace('\r','')))
open('final_result.txt','w',encoding='utf-8').write("\n".join(lines))
print("wrote final_result.txt")
