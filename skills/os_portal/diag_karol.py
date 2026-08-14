#!/usr/bin/env python3
import sys, os
sys.path.insert(0, r"C:/Users/GilesHugo/SES_Workspace/skills/os_portal")
os.chdir(r"C:/Users/GilesHugo/SES_Workspace/skills/os_portal")
import build_bom_tracker as m

purch = m.load_project_tasks(m.PURCH_DEPT)
build_tasks = m.load_project_tasks(m.BUILD_ORDERS)
opens = m.load_open_orders()
data = m.build_dataset(purch, build_tasks, opens)

print("karol_action count:", len(data["karol_action"]))
for k in data["karol_action"]:
    st = k.get("steel")
    print(f"  job={k['job']} subtasks={len(k['subtasks'])} steel={'YES' if st else 'NO'}")
    if st:
        print(f"     -> {st['task']['name']} completed={st['completed']} n_subs={st['n_subtasks']} open={st['open_subs']} imported={st['imported']}")
print("steel_by_job keys:", sorted(data["steel_by_job"].keys()))
