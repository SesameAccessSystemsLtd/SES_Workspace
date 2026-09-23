import re
html = open(r"C:\Users\GilesHugo\SES_Workspace\data\os_status\bom-tracker.html", encoding="utf-8").read()

# Split sections
def section(title_re):
    m = re.search(title_re + r'(.*?)(?=<h2|</body>)', html, re.S)
    return m.group(1) if m else ""

issued = section(r'I\s*s?Issued BOMs|Issued BOMs')
# fallback
if not issued:
    m = re.search(r'Issued BOMs(.*?)(?=<h2|</body>)', html, re.S)
    issued = m.group(1) if m else ""
print("=== HEADER (all h2) ===")
for h in re.findall(r'<h2[^>]*>(.*?)</h2>', html, re.S):
    print("  -", re.sub(r'<[^>]+>','',h).strip()[:90])

print()
print("=== Issued BOMs section: job numbers + NEW pills ===")
print("  contains 1580?", "1580" in issued)
jobs = re.findall(r'\b(\d{4})\b', issued)
print("  job tokens in Issued section:", sorted(set(jobs)))
print("  NEW pills in issued:", issued.count(">NEW<") + issued.count("pill new"))
print()
print("=== parts-without-pack ordering (job order as rendered) ===")
m2 = re.search(r'[Pp]arts without assembly pack(.*?)(?=<h2|</body>)', html, re.S)
seg2 = m2.group(1) if m2 else ""
order = re.findall(r'>\s*(\d{4})\s*<', seg2)
print("  order:", order[:20])
print("  1580 position (1-based):", (order.index('1580')+1) if '1580' in order else "MISSING")
print()
print("=== 'done·unmapped' markers ===", html.count("done·unmapped") + html.count("done&middot;unmapped"))
print("=== Winkel present anywhere? ===", "Winkel" in html)
