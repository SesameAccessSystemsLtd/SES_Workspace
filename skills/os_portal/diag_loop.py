#!/usr/bin/env python3
import sys, os, json, re
sys.path.insert(0, r"C:/Users/GilesHugo/SES_Workspace/skills/os_portal")
os.chdir(r"C:/Users/GilesHugo/SES_Workspace/skills/os_portal")
import build_bom_tracker as m

data = m.build_dataset(m.load_project_tasks(m.PURCH_DEPT),
                       m.load_project_tasks(m.BUILD_ORDERS), m.load_open_orders())
print("karol_action len:", len(data["karol_action"]))
for k in data["karol_action"]:
    print("  entry job:", k["job"], "n_subs:", len(k["subtasks"]))

# Replicate render's karol card building precisely
cards = ""
for k in data["karol_action"]:
    subs = ""
    for s in k["subtasks"]:
        btn = (f'<button class="acc" onclick="stageAction(\'{m._jsesc(json.dumps({"action":"run_bom_pipeline","job":k["job"],"control":k["ctl"]["name"],"control_gid":k["ctl"]["gid"],"sub_bom":s["name"],"sub_bom_gid":s["gid"],"dropbox_url":s["dropbox"]}))}\', \'BOM pipeline push\')">▶ Stage to pipeline</button>'
               if s.get("dropbox") else '<span class="muted">no Dropbox link</span>')
        subs += (f'<li><b style="color:var(--red)">OPEN</b> '
                 f'<a href="{m.ASANA_TASK_BASE}/{s["gid"]}">{m.esc(s["name"])}</a> '
                 f'<span class="muted">({m.esc(s.get("due_on") or "no due")})</span> · {btn}</li>')
    st = k.get("steel")
    if st:
        steel_txt = (f'<a>{st["task"]["name"]}</a>')
        steel_class = "ok"
    else:
        steel_txt = f'No steel for job {m.esj(k["job"])}'
        steel_class = "open"
    steel_box = f'<div class="steelbox {steel_class}">{steel_txt}</div>'
    cards += f'<div class="card"><h2><span class="num">{m.esj(k["job"])}</span> {m.esc(k["ctl"]["name"])}</h2><ul>{subs}</ul>{steel_box}</div>'
    print("  after building card for job", k["job"], "-> cards len:", len(cards), "| has this job:", k["job"] in cards)
print("FINAL cards card-divs:", cards.count('<div class="card">'))
for mm in re.finditer(r'num">([^<]+)</span>', cards):
    print("   num:", mm.group(1))
