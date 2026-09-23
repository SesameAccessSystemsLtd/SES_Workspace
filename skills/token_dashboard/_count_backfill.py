import sqlite3
DB = r"file:C:/Users/GilesHugo/SES_Workspace/db/ops.db?mode=ro"
c = sqlite3.connect(DB, uri=True); c.row_factory = sqlite3.Row

# parts_control has created_at (when the PC task/row first seen). Use it as the
# "added within the last month" window proxy (task creation date = Giles's intent).
print("=== linked parts (gid set) by created_at month ===")
for r in c.execute("""
    SELECT substr(created_at,1,7) ym, COUNT(*) n
    FROM parts_control
    WHERE build_assembly_pack_gid IS NOT NULL AND build_assembly_pack_gid <> ''
    GROUP BY ym ORDER BY ym DESC
"""):
    print("  ", r["ym"], r["n"])
print()
# within last 31 days (created_at) AND linked AND has a ref name
n31 = c.execute("""
    SELECT COUNT(*) FROM parts_control
    WHERE build_assembly_pack_gid IS NOT NULL AND build_assembly_pack_gid <> ''
      AND build_pack_ref_name IS NOT NULL AND build_pack_ref_name <> ''
      AND created_at >= date('now','-31 days')
""").fetchone()[0]
print("linked + ref_name + created_at within 31 days:", n31)
# total linked + ref_name (upper bound on backfill)
tot = c.execute("""
    SELECT COUNT(*) FROM parts_control
    WHERE build_assembly_pack_gid IS NOT NULL AND build_assembly_pack_gid <> ''
      AND build_pack_ref_name IS NOT NULL AND build_pack_ref_name <> ''
""").fetchone()[0]
print("linked + ref_name (upper bound):", tot)
print()
print("=== sample: distinct project_code of within-31-day candidates ===")
for r in c.execute("""
    SELECT project_code, COUNT(*) n FROM parts_control
    WHERE build_assembly_pack_gid IS NOT NULL AND build_assembly_pack_gid <> ''
      AND build_pack_ref_name IS NOT NULL AND build_pack_ref_name <> ''
      AND created_at >= date('now','-31 days')
    GROUP BY project_code ORDER BY n DESC
"""):
    print("  ", r["project_code"], r["n"])
