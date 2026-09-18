# OS Portal — VPS Hosting Setup Prompt & Test Plan

**Purpose:** a self-contained brief to (a) set up hosting of the SES OS Portal on the VPS, (b) keep the
HTML updated robustly from local, and (c) test exactly what works and what breaks — especially the
interactive push/nudge buttons.
**Date:** 2026-09-11 · **Related:** `plans/os-portal-hosting-plan-2026-09-11.md`, `reports/os-portal-hosting-assessment-2026-09-11.md`, AI Dev Work task `1218415960752148`.

---

## 0. ARCHITECTURE — accurate terminology

There is **NO Cloudflare Tunnel** on this VPS. The proven ingress (same as the webhook) is:

```
Internet → Cloudflare (DNS, orange-cloud) → VPS :443 → Traefik (reverse proxy, Let's Encrypt TLS)
                                                        ├─ hooks.sesameaccess.com → Asana webhook receiver (existing)
                                                        └─ os.sesameaccess.com    → static portal server (NEW)
```

- "The tunnel" = **Traefik routing + Cloudflare proxying**. Adding a site = drop a `.yml` in
  `/docker/traefik/dynamic/` (`file.watch=true` → hot-reload, no restart).
- **Direction of data:** local is the source of truth; the VPS is a **dumb target** that receives pushed HTML.
  Do NOT run portal-refresh as a VPS cron (Giles decision 2026-09-11 — avoids forking the source of truth).

---

## 1. WHAT TO SET UP ON THE VPS

### 1.1 Create the serve directory
```bash
mkdir -p /opt/ses/portal
# owned by root; holds the pushed portal HTML (index.html, bom-tracker.html, ...)
```

### 1.2 Static server (nginx container, host-networked, loopback-bound)
`/opt/ses/portal/docker-compose.yml`:
```yaml
services:
  portal:
    image: nginx:alpine
    container_name: ses-portal
    restart: unless-stopped
    network_mode: host          # so Traefik (host netns) can reach it on 127.0.0.1
    volumes:
      - /opt/ses/portal:/usr/share/nginx/html:ro
```
`/opt/ses/portal/nginx.conf` (listen loopback only):
```nginx
server {
    listen 127.0.0.1:8090;
    root /usr/share/nginx/html;
    index index.html;
    # never list directories
    autoindex off;
}
```
> mount nginx.conf in the compose file (`- /opt/ses/portal/nginx.conf:/etc/nginx/conf.d/default.conf:ro`).
> Port 8090 (chosen to avoid the webhook's 8099 / 8088).

### 1.3 Traefik route — `/docker/traefik/dynamic/portal.yml`
```yaml
http:
  routers:
    portal:
      rule: "Host(`os.sesameaccess.com`)"
      entryPoints: [websecure]
      service: portal-svc
      tls:
        certResolver: letsencrypt
    portal-http:
      rule: "Host(`os.sesameaccess.com`)"
      entryPoints: [web]
      service: portal-svc
  services:
    portal-svc:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:8090"
```

### 1.4 Cloudflare (dashboard — set BEFORE exposing)
1. **DNS:** `os.sesameaccess.com` → VPS IP, **Proxied (orange-cloud)**.
2. **Cloudflare Access** application on `os.sesameaccess.com`, two policies:
   - Policy A "Office" → **IP allowlist** (SES office egress IP) — zero login in-office.
   - Policy B "Giles remote" → **email OTP / SSO** for giles@sesameaccess.com.
3. Do NOT expose 8090/8099/8088/8642 in ufw. Only 22/80/443 stay open.
4. Cert issues automatically on first HTTPS hit (HTTP-01) once DNS points at the VPS.

---

## 2. LOCAL UPDATE MECHANISM (robust, push-based)

Portal HTML is generated locally by cron (Status Snapshot, BOM Tracker, Ops Oversight, Orders by Pack)
into `SES_Workspace/data/os_status/`. Keep that. Add:

### 2.1 Curate step (do NOT push the raw folder)
`data/os_status/` contains 22+ dated archives + `.hier_cache.json`. Push only the **current dashboards**:
`index.html, nav.html, bom-tracker.html, giles-worklist.html, ops-oversight.html, orders-by-pack.html, deployment-tracker.html`
→ into a staging dir `data/os_portal/`.

### 2.2 Push step — **use `scp`, NOT rsync** (no rsync in local git-bash; only scp)
Local script (runs AFTER the refresh jobs, one schedule):
```bash
# push_os_portal.sh  (local)
set -euo pipefail
STAGE="$HOME/SES_Workspace/data/os_portal"
HOST="vps"                       # ssh alias; key ~/.ssh/vps-1903976
ssh "$HOST" 'mkdir -p /opt/ses/portal'
scp -q "$STAGE"/*.html "$HOST:/opt/ses/portal/"     # overwrite semantics; idempotent
```
- **Idempotent:** re-running just overwrites identical files.
- **Atomic option:** scp to `/opt/ses/portal.tmp/` then `ssh vps 'mv ... && rsync-free swap'` if we want
  no half-written page — optional hardening.
- Schedule: a local cron/Hermes job **after** the last portal-refresh of the day (or every N minutes).

### 2.3 Health check
After push, `curl -s https://os.sesameaccess.com/health.html` (add a tiny generated `health.html` with a
build timestamp) → confirms the pushed page is live.

---

## 3. TEST MATRIX — what works vs what breaks

| # | What | Expected on VPS | Notes |
|---|---|---|---|
| T1 | Static dashboards render (index/bom-tracker/worklist/ops-oversight/orders-by-pack/deployment) | ✅ PASS | pure HTML, no backend |
| T2 | Links to Asana (`app.asana.com`) | ✅ PASS | absolute https links, work anywhere |
| T3 | Cloudflare Access challenge (off-network) | ✅ PASS | email OTP prompt |
| T4 | Cloudflare Access bypass (on office IP) | ✅ PASS | no login |
| T5 | TLS cert valid for os.sesameaccess.com | ✅ PASS | LE auto-issue |
| T6 | Push → live within one cycle | ✅ PASS | verify build timestamp changes |
| T7 | Push idempotent (re-run no diff) | ✅ PASS | scp overwrite |
| **T8** | **`bom-tracker.html` PUSH/NUDGE buttons** | 🔴 **FAILS as-is** | see below |
| **T9** | **Button → dispatch → Telegram nudge** | 🔴 **FAILS as-is** | depends on T8 |

### T8/T9 — the button problem (Giles flagged this correctly)
`bom-tracker.html` line 96: `var BOM_DISPATCH_URL = 'http://127.0.0.1:8765/dispatch';`
- The button JS POSTs to **`127.0.0.1:8765`** — in a browser that's **the user's own machine**, not the VPS.
- The `bom_dispatch_server.py` (which posts the Telegram nudge + writes the REQUESTS file) runs **locally only**.
- **Result:** served from the VPS, every colleague's button click hits their own laptop → fails (and the
  in-page alert "start bom_dispatch_server.py on the host" appears).

**Fix options for T8/T9 (pick Monday):**
- **(A) Expose the dispatch server via Traefik** too (e.g. `dispatch.sesameaccess.com` → local-equivalent
  endpoint) and set `BOM_DISPATCH_URL` to that absolute URL. The dispatch server must then live somewhere
  always-on (the VPS, or a tunnel back to local). ⚠️ security: it's a write/staging endpoint — must sit
  behind Cloudflare Access like the portal.
- **(B) Keep buttons local-only:** mark them disabled/read-only on the hosted portal; staff use the local
  copy for actions. Simplest, safest for v1.
- **(C) Rebuild the button action as a server-side portal endpoint** (bigger change).

**Recommendation for v1 go-live:** ship the **read-only dashboards** (T1–T7) publicly behind Access,
and **disable the interactive buttons on the hosted copy** (option B) until the dispatch path is
designed properly. That way "the portal is live for others to use" is achieved **without** the button
failure confusing users.

---

## 4. GO/NO-GO for Monday
- ✅ Traefik entrypoints + hot-reload + LE — present.
- ✅ SSH, scp, docker, disk — present.
- 🔴 Portal HTML not yet on the VPS; no static server; no `portal.yml`; no DNS/Access — all TODO (§1).
- 🔴 Buttons break when hosted (§3 T8/T9) — decide option A/B/C.
- **Gate order:** Cloudflare Access policy → apply portal.yml + nginx → first push → verify T1–T7 with
  buttons disabled → then decide button strategy.

---

## 5. Copy-paste PROMPT for the VPS work
> On the VPS (root): (1) `mkdir -p /opt/ses/portal`. (2) Create `/opt/ses/portal/docker-compose.yml`
> running `nginx:alpine`, `network_mode: host`, mounting `/opt/ses/portal` read-only, plus an
> `nginx.conf` listening `127.0.0.1:8090` with `autoindex off`. (3) `docker compose up -d`.
> (4) Create `/docker/traefik/dynamic/portal.yml` routing `Host(os.sesameaccess.com)` (web + websecure,
> tls certResolver letsencrypt) to `http://127.0.0.1:8090` — Traefik hot-reloads, do NOT restart it.
> (5) Confirm `curl -s http://127.0.0.1:8090/index.html` returns the portal locally on the VPS.
> (6) In Cloudflare, add proxied DNS `os.sesameaccess.com` → VPS IP, and an Access app with an
> IP-allowlist policy (office) + email-OTP policy (Giles). (7) From local, run the scp push of the
> curated HTML into `/opt/ses/portal/`. (8) Verify `https://os.sesameaccess.com` returns 200 + Access
> challenge, and that a re-push is idempotent. Do NOT open 8090/8099/8088/8642 in ufw.
