#!/usr/bin/env python3
"""
build_assembly_group_map.py
=====================================================================
Encode Giles' pack-code -> Assembly Group taxonomy, map it onto the
Golden Index packs, and produce:
  * assembly_group_map.csv     pack_code, group_name, group_option_gid
  * kan_assembly_groups.csv    KAN, packs, group_names, group_option_gids
  * a coverage report (which of the 77 golden packs have no group)

Assembly Group is a multi_enum custom field (1217486166709512) on the
Stock Visibility project; option GIDs below are the live values.
Unmapped packs are flagged, never guessed.
=====================================================================
"""
import csv, json, os

HERE = os.path.dirname(os.path.abspath(__file__))

# Live Assembly Group option GIDs (from the project).
GROUP_OPTION_GID = {
    "Lift": "1217486166709513",
    "Stairs": "1217486166709514",
    "Barriers": "1217486166709515",
    "Gates": "1217486166709516",
    "PIT TRIM": "1217486166709523",
    "Buttons": "1217486166709517",
    "Traversing": "1217486166709518",
    "Scissor stock": "1217486166709519",
    "PLC Stock": "1217486166709520",
    "GENERIC STOCK": "1217486166709521",
}

# Giles' taxonomy: his label -> canonical option name -> the pack codes.
TAXONOMY = {
    "GENERIC STOCK": ["1000.A", "1000.B", "1000.C", "1000.F", "1000.L"],
    "Stairs": ["1001.B", "1001.D", "1001.E", "1003", "1003.A", "1003.B",
               "2000.A", "2000.B", "2000.E", "2100.A", "2100.B", "2100.C",
               "2100.T", "3000.1", "3000.W", "6000.A", "6000.B", "6000.D"],
    "Lift": ["1004.B", "1004.F", "1004.R", "1004.T", "1006.A", "1006.B",
             "1006.C", "1006.J", "1055.A"],
    "PLC Stock": ["1008.A", "1008.C", "1008.D", "1008.H", "1008.J", "1008.N",
                  "1008.T", "1008.U", "1008.V", "1008.W", "1008.Y"],
    "Barriers": ["1009.A", "1009.B", "1009.E", "1009.F", "1009.G", "1009.H",
                 "1009.I", "1009.J", "1009.K", "1009.L", "1014.C", "1014.F",
                 "1014.H", "1015.C", "1024", "1029.A", "1034.B", "1070",
                 "1005.A", "1005.B", "1005.C"],
    "Scissor stock": ["1010.C", "1010.E", "1011.A", "1011.B", "1011.D", "1018"],
    "Buttons": ["1016.1", "1016.2", "1016.3", "1016.F", "1016.P", "1021.A", "1022"],
}

# pack code -> group name
PACK_TO_GROUP = {}
for group, codes in TAXONOMY.items():
    for c in codes:
        PACK_TO_GROUP[c.strip()] = group


def main():
    master = json.load(open(os.path.join(HERE, "golden_index_master.json")))["records"]
    golden_packs = sorted({p for r in master for p in r["assembly_packs"]})

    covered, uncovered = [], []
    for p in golden_packs:
        (covered if p in PACK_TO_GROUP else uncovered).append(p)

    # pack -> group map (golden packs only)
    with open(os.path.join(HERE, "assembly_group_map.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["pack_code", "group_name", "group_option_gid"])
        for p in golden_packs:
            g = PACK_TO_GROUP.get(p, "")
            w.writerow([p, g, GROUP_OPTION_GID.get(g, "")])

    # KAN -> groups
    with open(os.path.join(HERE, "kan_assembly_groups.csv"), "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(["KAN", "packs", "group_names", "group_option_gids", "unmapped_packs"])
        for r in sorted(master, key=lambda x: x["kan"]):
            packs = r["assembly_packs"]
            groups, gids, unmapped = [], [], []
            for p in packs:
                g = PACK_TO_GROUP.get(p)
                if g:
                    if g not in groups:
                        groups.append(g); gids.append(GROUP_OPTION_GID[g])
                else:
                    unmapped.append(p)
            w.writerow([r["kan"], " | ".join(packs), " | ".join(groups),
                        ",".join(gids), " | ".join(unmapped)])

    print(f"golden packs: {len(golden_packs)} | covered: {len(covered)} | UNCOVERED: {len(uncovered)}")
    if uncovered:
        print("UNCOVERED packs (need a group assignment):", uncovered)
    # sample KANs
    recs = {r["kan"]: r for r in master}
    for k in ["KAN-329", "KAN-244", "KAN-1010"]:
        packs = recs[k]["assembly_packs"]
        groups = []
        for p in packs:
            g = PACK_TO_GROUP.get(p)
            if g and g not in groups:
                groups.append(g)
        print(f"  {k}: packs={packs} -> groups={groups} gids={[GROUP_OPTION_GID[g] for g in groups]}")


if __name__ == "__main__":
    main()
