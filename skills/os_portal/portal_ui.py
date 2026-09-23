"""portal_ui.py — the single source of truth for SAS OS portal look and behaviour.

WHY THIS EXISTS
  As of 2026-09-18 each of the seven builders carried its own hand-rolled palette.
  Two had the three-theme switcher (build_os_status, build_deployment_tracker);
  five had a lone `:root` block and no switcher at all, so those pages could not
  change colour and drifted from the others on every edit. Buttons were styled with
  ~90-character inline `style=` attributes, so there was no way to change a button
  without editing every generator.

  Every builder should now do:

      from portal_ui import CSS, head, hero, btn, TRIGGER_JS

  and delete its local CSS constant. Class names here are a SUPERSET of the ones the
  existing pages already use (.wrap .hero .sub .badge .note .pill .sec .vendor .acc
  .steelbox .stack .tile .grid .cat .navbtn .muted .cnt), so swapping the constant in
  cannot drop a class a page relies on.

THEME CONTRACT
  Seven themes: light, sand, slate, ocean, forest, plum, neon. Applied as a class on
  <html> (`theme-<name>`; light is the bare :root).

  PER-PAGE MEMORY. Each page remembers its OWN theme, under
  `sas_os_theme::<page_id>`, so colour becomes wayfinding -- set BOM tracker to sand
  and the dev portal to plum and you know which page you are on before reading a
  word. A page seen for the first time falls back to `sas_os_theme:last` (whatever
  you last picked anywhere), so new pages arrive in a familiar colour rather than
  snapping back to light.

  Every colour in every page must come from a var() below -- never a literal hex in
  a builder.
"""
from __future__ import annotations

import html as _html
import json
import json as _json

THEME_KEY = "sas_os_theme"

# ── palette + components ────────────────────────────────────────────────────
CSS = """
/* Seven themes. Each page remembers its own, so colour becomes wayfinding:
   you learn "the amber one is BOM tracker" and recognise it before reading. */
:root{--bg:#f4f6fa;--card:#fff;--ink:#1a1a2e;--muted:#6b7280;--line:#e5e7eb;
--brand:#0f766e;--amber:#d97706;--red:#dc2626;--green:#16a34a;--blue:#2563eb;
--brand2:#0f766e;--hero-grad:linear-gradient(135deg,#0f172a 0%,#134e4a 100%);
--hover:#f9fafb;--subbg:#f8fafc;--accbg:#ecfdf5;--accink:#065f46;--onbrand:#fff}

:root.theme-sand{--bg:#faf6f0;--card:#fffdfa;--ink:#2b2118;--muted:#8a7660;--line:#e8dcc9;
--brand:#a8651f;--amber:#b45309;--red:#b91c1c;--green:#4d7c0f;--blue:#1d4ed8;
--brand2:#7c4a12;--hero-grad:linear-gradient(135deg,#3b2a17 0%,#7c4a12 100%);
--hover:#f5efe4;--subbg:#f7f2e9;--accbg:#fdf3e3;--accink:#7c4a12;--onbrand:#fff}

:root.theme-slate{--bg:#1b1f24;--card:#242a31;--ink:#e6edf3;--muted:#9099a6;--line:#343b44;
--brand:#58a6ff;--amber:#e3b341;--red:#f85149;--green:#3fb950;--blue:#58a6ff;
--brand2:#79c0ff;--hero-grad:linear-gradient(135deg,#12161a 0%,#2d3742 100%);
--hover:#2b323a;--subbg:#20262d;--accbg:#10243a;--accink:#79c0ff;--onbrand:#0d1117}

:root.theme-ocean{--bg:#0f1117;--card:#1a1d27;--ink:#e2e8f0;--muted:#94a3b8;--line:#2d3250;
--brand:#6c8ef7;--amber:#fbbf24;--red:#f87171;--green:#34d399;--blue:#6c8ef7;
--brand2:#a78bfa;--hero-grad:linear-gradient(135deg,#1e2a5e 0%,#2d1f6e 100%);
--hover:#21253a;--subbg:#1f2436;--accbg:#0c2b22;--accink:#34d399;--onbrand:#0f1117}

:root.theme-forest{--bg:#0d1512;--card:#15241f;--ink:#dcefe6;--muted:#7fa091;--line:#234034;
--brand:#2ea87e;--amber:#d9a441;--red:#e05c5c;--green:#3fc98f;--blue:#4d9de0;
--brand2:#6ddba8;--hero-grad:linear-gradient(135deg,#07140f 0%,#14452f 100%);
--hover:#1a2f27;--subbg:#112019;--accbg:#0a2a1e;--accink:#6ddba8;--onbrand:#07140f}

:root.theme-plum{--bg:#14101c;--card:#1e1830;--ink:#ece6f7;--muted:#9b8fb5;--line:#352a4d;
--brand:#b478ff;--amber:#f0a63c;--red:#ff6b81;--green:#4ad9a4;--blue:#7c93ff;
--brand2:#e0a3ff;--hero-grad:linear-gradient(135deg,#1a0b2e 0%,#4a1d6b 100%);
--hover:#271f3d;--subbg:#1a1428;--accbg:#2a1240;--accink:#e0a3ff;--onbrand:#14101c}

:root.theme-neon{--bg:#05060f;--card:#0d101f;--ink:#e6f1ff;--muted:#8a93b8;--line:#1e2a4a;
--brand:#00f0ff;--amber:#ffd100;--red:#ff3b6b;--green:#00ff9d;--blue:#5f7cff;
--brand2:#b14aff;--hero-grad:linear-gradient(135deg,#060014 0%,#1a0040 60%,#004b73 100%);
--hover:#131a33;--subbg:#0b0f24;--accbg:#001a1f;--accink:#00ff9d;--onbrand:#05060f}

*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
background:var(--bg);color:var(--ink);margin:0;line-height:1.5;
transition:background .4s,color .4s}
.wrap{max-width:1200px;margin:0 auto;padding:32px 24px 80px}
a{color:var(--brand)}

header.hero{background:var(--hero-grad);color:#fff;border-radius:16px;padding:30px;
margin-bottom:24px;transition:background .4s}
header.hero h1{margin:0 0 6px;font-size:28px}
header.hero .sub{opacity:.85;font-size:14px;margin-bottom:16px}
.badges{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.badge{background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.22);
color:#fff;border-radius:20px;padding:4px 12px;font-size:12px;font-weight:600}
.navbtn{display:inline-block;color:#fff;opacity:.85;text-decoration:none;font-size:13px}
.navbtn:hover{opacity:1}

.thememenu{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.tbtn{background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.25);color:#fff;
border-radius:20px;padding:5px 14px;font-size:12.5px;cursor:pointer;font-weight:600}
.tbtn.on{background:var(--brand);border-color:var(--brand);color:var(--onbrand)}
.tbtn .dot{display:inline-block;width:9px;height:9px;border-radius:50%;
margin-right:6px;vertical-align:middle}

/* ONE button component. No inline styles in any builder, ever. */
.btn{font:inherit;font-size:13px;font-weight:600;border-radius:8px;padding:6px 14px;
cursor:pointer;border:1px solid transparent;background:var(--brand);color:var(--onbrand);
transition:filter .15s,opacity .15s}
.btn:hover:not(:disabled){filter:brightness(1.12)}
.btn:disabled{opacity:.55;cursor:not-allowed}
.btn.ghost{background:transparent;border-color:var(--line);color:var(--ink)}
.btn.ghost:hover:not(:disabled){background:var(--hover)}
.btn.ok{background:var(--green)}
.btn.warn{background:var(--amber)}
.btn.danger{background:var(--red)}
.btn.sm{font-size:12px;padding:4px 10px}
.btnrow{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.acc{font:inherit;font-size:12.5px;font-weight:600;border-radius:8px;padding:4px 10px;
cursor:pointer;border:1px solid var(--brand);background:transparent;color:var(--brand)}
.acc:hover:not(:disabled){background:var(--accbg)}
.acc:disabled{opacity:.55;cursor:not-allowed}

.card{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:18px;margin-bottom:14px;transition:background .4s,border-color .4s}
.cat{margin-top:28px}
.cat h2{font-size:15px;text-transform:uppercase;letter-spacing:.6px;
color:var(--muted);margin:0 0 12px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;
display:block;text-decoration:none;color:var(--ink);
transition:transform .12s,border-color .2s,background .4s}
.tile:hover{transform:translateY(-2px);border-color:var(--brand)}
.tile .ico{font-size:22px;margin-bottom:8px}
.tile h3{margin:0 0 4px;font-size:15px;color:var(--ink)}
.tile p{margin:0;font-size:12.5px;color:var(--muted)}
.tile .tag,.tag{display:inline-block;background:color-mix(in srgb,var(--brand) 14%,transparent);
color:var(--brand);border-radius:6px;padding:2px 8px;font-size:11px;font-weight:600;margin-top:8px}

.muted{color:var(--muted)}
.note{background:var(--subbg);border:1px solid var(--line);border-left:3px solid var(--brand);
border-radius:8px;padding:10px 14px;font-size:13px;margin:12px 0}
.note.warn{border-left-color:var(--amber)}
.pill{display:inline-block;border-radius:20px;padding:2px 10px;font-size:11.5px;font-weight:600}
.pill.open{background:color-mix(in srgb,var(--blue) 15%,transparent);color:var(--blue)}
.pill.warn{background:color-mix(in srgb,var(--amber) 15%,transparent);color:var(--amber)}
.pill.over{background:color-mix(in srgb,var(--red) 15%,transparent);color:var(--red)}
.pill.ok{background:color-mix(in srgb,var(--green) 15%,transparent);color:var(--green)}
.stack{display:flex;flex-direction:column;gap:10px}
.steelbox{border-radius:8px;padding:7px 12px;font-size:12.5px;margin-top:8px;font-weight:600}
.steelbox.ok{background:color-mix(in srgb,var(--green) 12%,transparent);
border:1px solid var(--green);color:var(--green)}
.steelbox.open{background:color-mix(in srgb,var(--amber) 12%,transparent);
border:1px solid var(--amber);color:var(--amber)}

table{width:100%;border-collapse:collapse;font-size:13px;margin-top:8px}
th{text-align:left;color:var(--muted);font-weight:600;font-size:11.5px;
text-transform:uppercase;letter-spacing:.4px;padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
tr:hover td{background:var(--hover)}
code{background:var(--subbg);border:1px solid var(--line);border-radius:4px;
padding:1px 5px;font-size:12px}

details.sec{background:var(--card);border:1px solid var(--line);border-radius:12px;
padding:14px 18px;margin-bottom:12px;transition:background .4s}
details.sec summary{cursor:pointer;font-size:14.5px;font-weight:700;
color:var(--ink);user-select:none}
details.sec .cnt{color:var(--brand)}
details.vendor{margin-top:6px}
details.vendor summary{cursor:pointer;font-size:13px;color:var(--ink)}
@media(max-width:640px){.wrap{padding:20px 16px 60px}header.hero{padding:20px}}
"""

THEMES = [("light", "Light", "#f4f6fa"), ("sand", "Sand", "#d8a55f"),
          ("slate", "Slate", "#58a6ff"), ("ocean", "Ocean", "#6c8ef7"),
          ("forest", "Forest", "#2ea87e"), ("plum", "Plum", "#b478ff"),
          ("neon", "Neon", "#00f0ff")]
THEME_NAMES = [t[0] for t in THEMES]

THEME_JS = """
var SAS_THEMES = %s;
function sasThemeKey(){ return '%s::' + (window.SAS_PAGE_ID || 'default'); }
function setTheme(t){
  if(SAS_THEMES.indexOf(t) < 0) t = 'light';
  var r = document.documentElement;
  for(var i=0;i<SAS_THEMES.length;i++) r.classList.remove('theme-'+SAS_THEMES[i]);
  if(t !== 'light') r.classList.add('theme-'+t);
  try{ localStorage.setItem(sasThemeKey(), t);
       localStorage.setItem('%s:last', t); }catch(e){}
  var b = document.querySelectorAll('.tbtn');
  for(var i=0;i<b.length;i++) b[i].classList.toggle('on', b[i].dataset.theme === t);
}
function initTheme(){
  var t = 'light';
  try{ t = localStorage.getItem(sasThemeKey())
           || localStorage.getItem('%s:last') || 'light'; }catch(e){}
  setTheme(t);
}
""" % (json.dumps(THEME_NAMES), THEME_KEY, THEME_KEY, THEME_KEY)


def theme_boot(page_id: str) -> str:
    """Applied before first paint, so a dark page never flashes white on load.
    Must know the page id, hence a function rather than a constant."""
    return (f"<script>window.SAS_PAGE_ID={_json.dumps(page_id)};"
            "(function(){try{var k='%s::'+window.SAS_PAGE_ID;"
            "var t=localStorage.getItem(k)||localStorage.getItem('%s:last');"
            "if(t&&t!=='light')document.documentElement.classList.add('theme-'+t);"
            "}catch(e){}})();</script>" % (THEME_KEY, THEME_KEY))


THEME_SWITCHER = "".join(
    f'<button class="tbtn" data-theme="{k}" onclick="setTheme(\'{k}\')" '
    f'title="Remembered for this page only">'
    f'<span class="dot" style="background:{c}"></span>{n}</button>'
    for k, n, c in THEMES)
THEME_SWITCHER = f'<div class="thememenu">{THEME_SWITCHER}</div>'

# ── the single trigger path every action button uses ────────────────────────
# RELATIVE URL. Served from bom_dispatch_server (127.0.0.1:8765) it resolves to
# that server; opened as file:// it resolves to nothing, so the buttons render
# disabled with an honest explanation instead of failing on click. That one change
# fixes the local "buttons don't work" report AND makes the pages hostable
# unmodified -- no build-time transform, local and hosted copies byte-identical.
TRIGGER_JS = """
var SAS_LIVE = (location.protocol !== 'file:');
var SAS_DISPATCH = '/dispatch';
function sasDisableOffline(){
  if(SAS_LIVE) return;
  var b=document.querySelectorAll('[data-action]');
  for(var i=0;i<b.length;i++){
    b[i].disabled=true;
    b[i].title='Open this page at http://127.0.0.1:8765/ to use the action buttons '
      + '(a file:// page has no server to post to).';
  }
  var n=document.getElementById('offline-note');
  if(n) n.style.display='block';
}
function sasTrigger(el, payload, kind, done){
  if(!SAS_LIVE){ return; }
  var label = el ? el.textContent : '';
  if(el){ el.disabled=true; el.textContent='Working…'; }
  fetch(SAS_DISPATCH, {method:'POST', headers:{'Content-Type':'application/json'},
                       body: (typeof payload==='string'?payload:JSON.stringify(payload))})
    .then(function(r){ return r.json().then(function(j){ return {ok:r.ok, j:j}; }); })
    .then(function(res){
      if(res.ok && res.j && res.j.ok){
        if(el){ el.textContent = done || 'Done \\u2713'; }
        if(typeof sasOnSuccess==='function') sasOnSuccess(kind,res.j,payload);
      } else {
        if(el){ el.disabled=false; el.textContent='\\u21ba retry'; }
        alert((kind||'Action')+' FAILED: '+((res.j&&res.j.error)||'unexpected response'));
      }
    })
    .catch(function(e){
      if(el){ el.disabled=false; el.textContent='\\u21ba retry'; }
      alert('Server unreachable \\u2014 is bom_dispatch_server.py running?\\n'+e);
    });
}
"""


def esc(s) -> str:
    return _html.escape(str(s if s is not None else ""))


def btn(label, onclick="", kind="", *, small=False, disabled=False,
        action=False, title="") -> str:
    """The ONLY way a builder should emit a button. `action=True` marks it as
    server-dependent so it is auto-disabled on a file:// page."""
    cls = " ".join(x for x in ["btn", kind, "sm" if small else ""] if x)
    return (f'<button class="{cls}"'
            + (f' onclick="{onclick}"' if onclick else "")
            + (' data-action="1"' if action else "")
            + (f' title="{esc(title)}"' if title else "")
            + (" disabled" if disabled else "")
            + f">{esc(label)}</button>")


def head(title: str, page_id: str = "default", extra_css: str = "",
         extra_js: str = "") -> str:
    """page_id keys this page's remembered theme. Give every page a stable, distinct
    one (e.g. "bom-tracker") -- reusing an id makes two pages share a colour."""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
{theme_boot(page_id)}
<style>{CSS}{extra_css}</style>
<script>{THEME_JS}{TRIGGER_JS}{extra_js}</script>
</head>
<body onload="initTheme();sasDisableOffline();"><div class="wrap">"""


def hero(title: str, sub: str = "", badges=None, back="nav.html",
         actions: str = "") -> str:
    b = "".join(f'<span class="badge">{esc(x)}</span>' for x in (badges or []))
    return f"""<header class="hero">
<h1>{esc(title)}</h1>
<div class="sub">{esc(sub)}</div>
{f'<a href="{esc(back)}" class="navbtn">&larr; SAS OS portal</a>' if back else ''}
{THEME_SWITCHER}
{f'<div class="badges">{b}</div>' if b else ''}
{f'<div class="btnrow" style="margin-top:12px">{actions}</div>' if actions else ''}
</header>
<div class="note warn" id="offline-note" style="display:none">
Opened as a local file, so the action buttons are disabled. Open
<code>http://127.0.0.1:8765/data/os_status/</code> to use them.</div>"""


def foot() -> str:
    return "</div></body></html>"
