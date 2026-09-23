import sqlite3
DB = r"C:\Users\GilesHugo\SES_Workspace\db\ops.db"
c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
def q(sql,args=()): return [dict(r) for r in c.execute(sql,args)]
def s(v): return "" if v is None else str(v)

print("=== 1595 Winkel Bearing part rows ===")
rows=q("""SELECT sesame_pn,part_name,stock_action,build_assembly_pack_gid,build_pack_ref_name,
                 association_status,is_completed,order_status,asana_task_gid,created_at,completed_at
          FROM parts_control WHERE sesame_pn LIKE '1595%' AND (sesame_pn LIKE '%Winkel%' OR part_name LIKE '%Winkel%')""")
for r in rows:
    print(f"  pn={s(r['sesame_pn'])}")
    print(f"    name={s(r['part_name'])}")
    print(f"    stock_action={s(r['stock_action'])} | pack_gid={s(r['build_assembly_pack_gid'])} | pack_ref={s(r['build_pack_ref_name'])}")
    print(f"    assoc={s(r['association_status'])} | done={r['is_completed']} | order_status={s(r['order_status'])}")
    print(f"    task_gid={s(r['asana_task_gid'])} created={s(r['created_at'])} compl={s(r['completed_at'])}")
print()
print("=== all 1595 parts with 'Bearing' or Winkel-ish, to see siblings ===")
rows2=q("""SELECT sesame_pn,part_name,build_assembly_pack_gid,build_pack_ref_name,is_completed,association_status
           FROM parts_control WHERE sesame_pn LIKE '1595.1000%' OR part_name LIKE '%Winkel%' ORDER BY sesame_pn""")
for r in rows2[:40]:
    print(f"  {s(r['sesame_pn']):26s} | pack_gid={s(r['build_assembly_pack_gid']):20s} | ref={s(r['build_pack_ref_name'])[:34]:34s} | done={r['is_completed']} | {s(r['part_name'])[:24]}")
print()
print("=== build_assembly_packs rows for 1595.1006 ===")
rows3=q("""SELECT pack_reference,asana_task_gid,is_completed,project_name FROM build_assembly_packs
           WHERE pack_reference LIKE '1595.1006%' OR pack_reference LIKE '1595.1000%'""")
for r in rows3:
    print(f"  {s(r['pack_reference']):40s} gid={s(r['asana_task_gid'])} done={r['is_completed']} proj={s(r['project_name'])}")
