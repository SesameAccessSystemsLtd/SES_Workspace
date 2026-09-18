#!/usr/bin/env python3
import sys, os, json, re
sys.path.insert(0, r"C:/Users/GilesHugo/SES_Workspace/skills/os_portal")
os.chdir(r"C:/Users/GilesHugo/SES_Workspace/skills/os_portal")
import build_bom_tracker as m

data = m.build_dataset(m.load_project_tasks(m.PURCH_DEPT),
                       m.load_project_tasks(m.BUILD_ORDERS), m.load_open_orders())
k = data["karol_action"][0]  # 1595
print("k =", k["job"], k["ctl"]["name"])
subs = ""
for s in k["subtasks"]:
    btn = (f'<button class="acc" onclick="stageAction(\'{m._jsesc(json.dumps({"action":"run_bom_pipeline","job":k["job"],"control":k["ctl"]["name"],"control_gid":k["ctl"]["gid"],"sub_bom":s["name"],"sub_bom_gid":s["gid"],"dropbox_url":s["dropbox"]}))}\', \'BOM pipeline push\')">▶ Stage to pipeline</button>'
           if s.get("dropbox") else '<span class="muted">no Dropbox link</span>')
    subs += (f'<li><b style="color:var(--red)">OPEN</b> '
             f'<a href="{m.ASANA_TASK_BASE}/{s["gid"]}">{m.esc(s["name"])}</a> '
             f'<span class="muted">({m.esc(s.get("due_on") or "no due")})</span> · {btn}</li>')
    print("  sub li len:", len(subs))
st = k.get("steel")
if st:
    steel_txt = (f"OK {st['task']['name']}")
    steel_class = "ok"
else:
    steel_txt = f'NO for job {m.esj(k["job"])}'
    steel_class = "open"
steel_box = f'<div class="steelbox {steel_class}">{steel_txt}</div>'
card = f'<div class="card"><h2><span class="num">{m.esj(k["job"])}</span> {m.esc(k["ctl"]["name"])}</h2><ul>{subs}</ul>{steel_box}</div>'
print("CARD LENGTH:", len(card))
print("card starts with card div:", card.startswith('<div class="card">'))
print("CARD HEAD:", card[:90])
