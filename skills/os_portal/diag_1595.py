#!/usr/bin/env python3
import sys, os, json
sys.path.insert(0, r"C:/Users/GilesHugo/SES_Workspace/skills/os_portal")
os.chdir(r"C:/Users/GilesHugo/SES_Workspace/skills/os_portal")
import build_bom_tracker as m

data = m.build_dataset(m.load_project_tasks(m.PURCH_DEPT),
                       m.load_project_tasks(m.BUILD_ORDERS), m.load_open_orders())
print("karol_action jobs in order:", [k["job"] for k in data["karol_action"]])
# Try to build the 1595 card HTML manually exactly as the render does
k = next(k for k in data["karol_action"] if k["job"] == "1595")
print("1595 control:", k["ctl"]["name"])
print("1595 subs:", [(s["name"], bool(s.get("dropbox"))) for s in k["subtasks"]])
# reproduce: does building the button payload for 1595's sub throw?
for s in k["subtasks"]:
    try:
        payload = json.dumps({"action":"run_bom_pipeline","job":k["job"],"control":k["ctl"]["name"],
                              "control_gid":k["ctl"]["gid"],"sub_bom":s["name"],"sub_bom_gid":s["gid"],
                              "dropbox_url":s["dropbox"]})
        escp = m._jsesc(payload)
        print(f"  sub '{s['name']}' payload OK, jsesc len={len(escp)}")
    except Exception as e:
        print(f"  ! sub '{s['name']}' ERROR: {e}")
        raise
