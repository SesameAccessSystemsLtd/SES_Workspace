import sqlite3

DB = r"C:\Users\GilesHugo\SES_Workspace\db\ops.db"
c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
def q(sql, args=()): return [dict(r) for r in c.execute(sql, args)]
def s(v): return "" if v is None else str(v)

print("========== 1580 unlinked parts (no build_assembly_pack_gid) ==========")
rows = q("""SELECT sesame_pn, part_name, stock_action, build_assembly_pack_gid,
                   build_pack_ref_name, association_status, is_completed, order_status,
                   created_at, first_seen_at, asana_task_gid
            FROM parts_control
            WHERE (project_code='1580' OR sesame_pn LIKE '1580.%')
              AND (build_assembly_pack_gid IS NULL OR build_assembly_pack_gid='')
            ORDER BY created_at""")
print(f"count: {len(rows)}")
for r in rows[:50]:
    print(f"  {s(r['sesame_pn']):26s} | {s(r['part_name'])[:28]:28s} | act={s(r['stock_action']):22s} | assoc={s(r['association_status']):12s} | done={r['is_completed']} | created={s(r['created_at'])[:19]}")

print()
print("========== 1580 linked parts (HAS pack) ==========")
rows2 = q("""SELECT sesame_pn, part_name, stock_action, build_assembly_pack_gid, build_pack_ref_name,
                    is_completed, created_at FROM parts_control
             WHERE (project_code='1580' OR sesame_pn LIKE '1580.%')
               AND build_assembly_pack_gid IS NOT NULL AND build_assembly_pack_gid<>''
             ORDER BY created_at""")
for r in rows2:
    print(f"  {s(r['sesame_pn']):26s} | pack_gid={s(r['build_assembly_pack_gid'])} | ref={s(r['build_pack_ref_name'])[:30]} | done={r['is_completed']}")

print()
print("========== association_status value distribution (all projects) ==========")
for r in q("SELECT association_status, COUNT(*) n FROM parts_control GROUP BY association_status ORDER BY n DESC"):
    print(f"  {s(r['association_status']):20s} {r['n']}")
