"""Inject a 'back to portal' nav button into each HTML page, right after its
opening <body...> tag. Idempotent: skips pages already containing the marker."""
import io, re, sys

NAV_MARKER = '<!-- SAS-OS-NAV -->'

BUTTONS = {
    # absolute path -> (relative href to data/os_status/nav.html)
    r"C:/Users/GilesHugo/SES_Workspace/data/os_status/index.html": "nav.html",
    r"C:/Users/GilesHugo/SES_Workspace/data/os_status/america-one-production-report-2026-08-03.html": "nav.html",
    r"C:/Users/GilesHugo/SES_Workspace/views/stock_management.html": "../data/os_status/nav.html",
    r"C:/Users/GilesHugo/SES_Workspace/views/memory_architecture.html": "../data/os_status/nav.html",
    r"C:/Users/GilesHugo/SES_Workspace/views/roadblock_map.html": "../data/os_status/nav.html",
    r"C:/Users/GilesHugo/SES_Workspace/views/graph.html": "../data/os_status/nav.html",
    r"C:/Users/GilesHugo/Downloads/Production_Delay_Analysis_Report.html": "../SES_Workspace/data/os_status/nav.html",
    r"C:/Users/GilesHugo/Downloads/Stock Management System — Sesame Access.html": "../SES_Workspace/data/os_status/nav.html",
    r"C:/Users/GilesHugo/Downloads/Stock Management System — Sesame Access (1).html": "../SES_Workspace/data/os_status/nav.html",
}

def button_html(href):
    # Fixed top-right, dark pill; inline styles so it needs no external CSS.
    return ('\n' + NAV_MARKER + '\n'
            '<a href="' + href +
            '" style="position:fixed;top:16px;right:16px;z-index:9999;'
            'background:#0f172a;color:#fff;font:600 13px -apple-system,\'Segoe UI\',Roboto,sans-serif;'
            'text-decoration:none;padding:8px 16px;border-radius:20px;'
            'box-shadow:0 2px 8px rgba(0,0,0,.25);'
            'border:1px solid rgba(255,255,255,.25)">&larr; SAS OS portal</a>\n')

def inject(path, href):
    try:
        s = open(path, encoding="utf-8", errors="replace").read()
    except FileNotFoundError:
        print(f"  SKIP (missing): {path}")
        return
    if NAV_MARKER in s:
        print(f"  already-has-nav: {path}")
        return
    m = re.search(r"<body[^>]*>", s)
    if not m:
        # fallback: insert right before </head>
        m2 = s.rfind("</head>")
        if m2 == -1:
            print(f"  NO-BODY-OR-HEAD: {path}")
            return
        s = s[:m2] + button_html(href) + s[m2:]
    else:
        idx = m.end()
        s = s[:idx] + button_html(href) + s[idx:]
    open(path, "w", encoding="utf-8").write(s)
    print(f"  injected: {path}")

if __name__ == "__main__":
    for path, href in BUTTONS.items():
        inject(path, href)
    print("DONE")
