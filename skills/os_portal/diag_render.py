#!/usr/bin/env python3
import sys, os
sys.path.insert(0, r"C:/Users/GilesHugo/SES_Workspace/skills/os_portal")
os.chdir(r"C:/Users/GilesHugo/SES_Workspace/skills/os_portal")
import build_bom_tracker as m

data = m.build_dataset(m.load_project_tasks(m.PURCH_DEPT),
                       m.load_project_tasks(m.BUILD_ORDERS), m.load_open_orders())
html = m.render(data, m.NEW_RELEASE, "2026-08-13")
# find karol stack in freshly-rendered html
i = html.find("Karol sub-BOMs needing action")
start = html.find('<div class="stack">', i)
end = html.find('</div></details>', start)
seg = html[start:end]
print("NEWLY RENDERED karol stack cards:", seg.count('<div class="card">'))
for mm in __import__('re').finditer(r'<div class="card"><h2><span class="num">([^<]+)</span>', seg):
    print("  CARD:", mm.group(1))
print("1595 in stack:", "1595" in seg[:1500])
