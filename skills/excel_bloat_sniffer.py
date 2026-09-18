#!/usr/bin/env python3
"""
Excel workbook bloat-sniffer (no Excel, no extra deps -- pure stdlib zipfile).

WHY: Workbook freezes (like "Steel Profile CHECKER V5.1") are usually one big
worksheet carrying hundreds of thousands of FORMULA CELLS in rows BEYOND its
live data -- a "dead formula tail". Excel has to load, style, calc-chain and
recalculate all of it on every open/edit.

This reads each .xlsm/.xlsx/.xlsb and reports, per sheet:
    live_rows      = highest row that ACTUALLY holds content/value
    eff_last       = Excel's effective used range ("LastCell") extent in rows
    bloat_rows     = eff_last - live_rows   (the dead empty tail Excel still tracks)
    dead_formulas  = formula cells sitting in rows > live_rows
    total_formulas = all formula cells on the sheet
    sheetXML KB    = uncompressed sheet XML (size proxy for styling/format load)

USAGE:
    python excel_bloat_sniffer.py "<file-or-folder>"
Optionally hide tidy sheets:   --min-bloat 5000   (only list sheets over N bloat rows)
READ-ONLY: never modifies any workbook.
"""

import sys, os, re, zipfile

CELL_RX  = re.compile(r'<c r="([A-Z]+)(\d+)"')
ROW_RX   = re.compile(r'<row r="(\d+)"')
DF_RX    = re.compile(r'<c r="([A-Z]+)(\d+)"[^>]*>\s*<f')   # cells that carry a formula
SHEETX_RX = re.compile(r'Id="(rId\d+)"[^>]*Target="([^"]*worksheets/[^"]+)"')
WB_SHEET_RX = re.compile(r'<sheet name="([^"]*)"[^>]*r:id="(rId\d+)"')

def colnum(letters):
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n

def worst_score(s):
    # dominant predictor of Excel open/edit pain: total formula cells a sheet forces,
    # plus macro cells sitting below genuine live content (dead tail), plus XML bulk (formatting)
    return (s["total_f"] // 50) + max(s["bloat"], 0) + (s["xmlKB"] // 4)


def analyze(path):
    out = {"path": path}
    try:
        z = zipfile.ZipFile(path)
    except Exception as e:
        out["error"] = "not a zip/xlsx (%s)" % e
        return out

    names = z.namelist()
    sheet_files = [n for n in names if n.startswith("xl/worksheets/") and n.endswith(".xml")]
    if not sheet_files:
        out["error"] = "no worksheets"
        return out

    # sheet display-name -> file, in workbook order
    disp = []
    try:
        wb = z.read("xl/workbook.xml").decode("utf-8", "replace")
        rel = z.read("xl/_rels/workbook.xml.rels").decode("utf-8", "replace")
        rid_tgt = dict(SHEETX_RX.findall(rel))
        for nm, rid in WB_SHEET_RX.findall(wb):
            t = rid_tgt.get(rid)
            if t:
                base = "xl/" + t.split("worksheets/")[0] + "worksheets/" + t.split("worksheets/")[-1]
                if base in names:
                    disp.append((nm, base))
    except Exception:
        pass
    if not disp:
        # fallback: sort sheet files by name
        disp = [("", n) for n in sorted(sheet_files)]

    total_xml = 0
    sheets = []
    for nm, base in disp:
        try:
            xml = z.read(base).decode("utf-8", "replace")
        except Exception:
            continue
        total_xml += len(xml)

        dm = re.search(r'<dimension ref="([^"]+)"', xml)
        dim = dm.group(1) if dm else ""
        decl_rows = 0
        m = re.search(r':([A-Z]+)(\d+)"', dim)
        if m:
            decl_rows = int(m.group(2))

        cells = CELL_RX.findall(xml)
        live = max((int(r) for _, r in cells), default=0) if cells else 0
        maxcol = max((colnum(c) for c, _ in cells), default=0) if cells else 0

        rowtags = [int(x) for x in ROW_RX.findall(xml)]
        eff_last = max([live] + rowtags)

        allf = DF_RX.findall(xml)
        total_f = len(allf)
        dead_f = sum(1 for _, r in allf if live and int(r) > live)

        bloat = eff_last - live
        sheets.append({
            "name": nm or base, "xmlKB": round(len(xml) / 1024),
            "live": live, "eff_last": eff_last, "decl_rows": decl_rows,
            "bloat": bloat, "dead_f": dead_f, "total_f": total_f,
            "maxcol": maxcol,
        })

    sheets.sort(key=lambda s: s["total_f"] + max(s["bloat"], 0) * 2 + s["xmlKB"], reverse=True)
    out.update(file_MB=round(os.path.getsize(path) / 1048576, 2),
               nsheets=len(sheets), xml_MB=round(total_xml / 1048576, 2), sheets=sheets)
    return out


def main():
    sysargv = sys.argv[1:]
    min_bloat = 0
    target = None
    i = 0
    while i < len(sysargv):
        a = sysargv[i]
        if a == "--min-bloat":
            i += 1
            if i < len(sysargv):
                try: min_bloat = int(sysargv[i])
                except ValueError: pass
        else:
            target = a
        i += 1
    if not target:
        print(__doc__)
        return

    files = []
    if os.path.isdir(target):
        for root, _, fs in os.walk(target):
            for f in fs:
                if not f.startswith("~$") and f.lower().endswith((".xlsm", ".xlsx", ".xlsb")):
                    files.append(os.path.join(root, f))
    elif os.path.isfile(target):
        files = [target]
    else:
        print("Path not found:", target)
        return
    if not files:
        print("No .xlsx/.xlsm/.xlsb found under:", target)
        return

    print("Scanning %d workbook(s)..." % len(files))
    reps = []
    for f in files:
        try:
            reps.append(analyze(f))
        except Exception as e:
            reps.append({"path": f, "error": str(e)})
    reps.sort(key=lambda r: -(sum(max(0, s["dead_f"]) + max(0, s["bloat"]) for s in r.get("sheets", [])) if not r.get("error") else -1), reverse=True)

    lvl_names = {0: "OK", 1: "MILD", 2: "HEAVY", 3: "SEVERE"}
    for r in reps:
        if r.get("error"):
            print("\n[SKIP] %s -- %s" % (os.path.basename(r["path"]) if False else r["path"], r["error"]))
            continue
        worst = r["sheets"][0] if r["sheets"] else None
        name = os.path.basename(r["path"])
        level = 0
        line = ""
        if worst:
            wf  = worst["total_f"]          # formula cells Excel must evaluate
            blo = max(worst["bloat"], 0)    # rows tracked below genuine content
            xm  = worst["xmlKB"]            # raw sheet size / formatting bulk
            level = 3 if (wf > 100000 or xm > 8000 or blo > 20000) else \
                    2 if (wf > 20000 or xm > 1500 or blo > 4000) else \
                    1 if (wf > 1000 or blo > 300) else 0
            if blo == 0 and wf > 0:
                blo_expr = "contiguous full-column formulas down to row %d" % worst["eff_last"]
            else:
                blo_expr = "dead tail of %d rows" % blo
            if worst["name"]:
                line = ("  worst '%s': %d formula cells, %s, liveRows=%d effLastRow=%d xml=%dKB"
                        % (worst["name"], worst["total_f"], blo_expr, worst["live"], worst["eff_last"], worst["xmlKB"]))
        print("\n[%s] %s  (%s sheets, %.2f MB file, %.1f MB sheetXML)" % (lvl_names[level], name, r["nsheets"], r["file_MB"], r["xml_MB"]))
        if line:
            print(line)
        if min_bloat <= 0:
            print("  # sheet                 liveRows effLast bloatRows deadFm totalFm xmlKB")
            for s in r["sheets"]:
                nm = s["name"][:18]
                print("  %-22s %8d %8d %9d %7d %7d %6d" % (nm, s["live"], s["eff_last"], max(s["bloat"], 0), s["dead_f"], s["total_f"], s["xmlKB"]))
        else:
            flagged = [s for s in r["sheets"] if s["bloat"] >= min_bloat]
            for s in flagged:
                print("    flagged '%s' bloatRows=%d deadFormulas=%d xml=%dKB" % (s["name"], s["bloat"], s["dead_f"], s["xmlKB"]))


if __name__ == "__main__":
    main()
