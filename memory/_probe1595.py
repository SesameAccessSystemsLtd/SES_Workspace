import sqlite3
con = sqlite3.connect('db/ops.db')
cur = con.cursor()

print('=== 1595 parts_control full state ===')
row = cur.execute(
    "SELECT COUNT(*), SUM(is_completed), SUM(CASE WHEN build_assembly_pack_gid IS NOT NULL THEN 1 ELSE 0 END), "
    "SUM(CASE WHEN association_status='linked' THEN 1 ELSE 0 END) "
    "FROM parts_control WHERE sesame_pn LIKE '1595.%'"
).fetchone()
print(f'total={row[0]}, completed={row[1]}, with_pack_gid={row[2]}, linked={row[3]}')

print()
print('=== 1595 bom_requirements by source ===')
for r in cur.execute(
    "SELECT source_bom_file, COUNT(*), SUM(CASE WHEN status='in_stock' THEN 1 ELSE 0 END) "
    "FROM bom_requirements WHERE build_ref LIKE '%1595%' OR source_bom_file LIKE '1595%' GROUP BY source_bom_file"
):
    print(' ', r)

print()
print('=== 1595 open parts_control sample (is_completed=0) ===')
for r in cur.execute(
    "SELECT sesame_pn, part_name, stock_action, build_assembly_pack_gid FROM parts_control "
    "WHERE sesame_pn LIKE '1595.%' AND is_completed=0 LIMIT 10"
):
    print(' ', r)

print()
print('=== 1595 distinct sesame_pn pack prefixes present in parts_control ===')
for r in cur.execute(
    "SELECT SUBSTR(sesame_pn, 1, 11) AS pfx, COUNT(*) c FROM parts_control "
    "WHERE sesame_pn LIKE '1595.%' GROUP BY pfx ORDER BY c DESC LIMIT 15"
):
    print(' ', r)
