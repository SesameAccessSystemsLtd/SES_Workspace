# OS Portal Hosting — Assessment & Insight (2026-09-11)

**Author:** Hermes · **Requested by:** Giles · **Status:** Assessment complete; deployment staged for Monday review
**Related:** `plans/os-portal-hosting-plan-2026-09-11.md` (technical recipe) · `plans/os-portal-and-guardrails-outline-2026-09-11.md` (approach)

---

## 1. The question
"Now that Cloudflare + webhooks are plugged into the local database, can we host the SES OS Portal on an
open dashboard for others in the business — with a password/IP gate so it's not open to everyone?"

## 2. What was verified (read-only recon over SSH, nothing changed)

### VPS architecture (Hostinger, ssh alias `vps`)
```
Internet → Cloudflare (DNS, orange-cloud) → VPS :443 → Traefik (Let's Encrypt)
                                                        ├─ hooks.sesameaccess.com → Asana webhook receiver v2 (container)
                                                        └─ (proposed) os.sesameaccess.com → static portal server
```
- **Front door = Traefik** (`traefik-traefik-1`). Dynamic routes in `/docker/traefik/dynamic/*.yml`,
  `file.watch=true` → **hot-reload, no restart**. One Let's Encrypt resolver (HTTP-01).
- **hooks.sesameaccess.com** → Asana webhook receiver **v2** (cutover 2026-09-11, DETECT mode, kill-switch,
  guardrails, self-healing route-sync timer). Webhook `1218404848667646` registered. This is the
  "Cloudflare webhook into Asana."
- ufw allows 22/80/443. Receiver binds loopback/bridge only — never directly exposed.
- VPS has its own Hermes gateway (`127.0.0.1:8642`) and a synced `/opt/ses/workspace`.

### OS Portal (what we'd host)
- **Static HTML dashboards** in `SES_Workspace/data/os_status/` (28 MB): `index.html`, `bom-tracker.html`,
  `giles-worklist.html`, `ops-oversight.html`, `orders-by-pack.html`, `deployment-tracker.html`, `nav.html`.
- Regenerated from **local** `ops.db` by local cron jobs (Status Snapshot `c1d757352bb7`, BOM Tracker
  `57fbe5f8d633`, Ops Oversight `47fbad9b5a8f`, Orders by Pack).
- The folder ALSO holds 22+ dated archives + a 550 KB `.hier_cache.json` → **must serve a curated subset**,
  not the raw directory (business data + internal hierarchy would otherwise be exposed).

## 3. Can we host it? — YES
The portal is already static HTML, so hosting = serve files behind a gate. Reuses the exact Traefik pattern
already proven for the webhook. Two new pieces only: a static server on the VPS + a `portal.yml` route.

## 4. Security (Giles's IP preference, done correctly)
- **Cloudflare Access** on the portal hostname with **two policies**:
  - Policy A — **IP-allowlist** (SES office egress) → zero login in the office.
  - Policy B — **email-OTP / SSO** for Giles → remote access still works.
- Rationale: IP-only would lock out mobile/remote (Giles spotted this himself). Baseline = OTP + IP bypass,
  so it can never lock him out.

## 5. Live-ness decision (Giles, 2026-09-11)
**Run the portal generation LOCALLY; PUSH the generated HTML to the hosted server.**
- Do **NOT** run portal-refresh as a cron on the VPS.
- Reason: all source data is the **local** `ops.db`; a VPS cron forks the source of truth → confusion.
- Consequence: the VPS is a **dumb target** — receives pushed HTML, serves it, generates nothing.

## 6. Evidence / what was NOT changed
- Read: VPS containers (`docker ps`), Traefik dynamic config, webhook-receiver docs (V2-CUTOVER, DEPLOYMENT,
  rules.json), the local `os_status/` portal files, `PATH_CONFIG.yaml`.
- **Nothing on the VPS was modified.** No Traefik route added, no Cloudflare change, no deploy.
- Local: curated preview staged at `data/os_portal_preview_20260911/` (7 dashboards, 1.9 MB),
  served loopback `http://127.0.0.1:8899/` for review.

## 7. Two guardrails raised by Giles (approach in the companion outline doc)
1. **External-write identification** — any write to a path outside the local workspace (esp. VPS over SSH)
   must be explicitly flagged; default assumption is LOCAL. (VPS has its own `ops.db`; a path bug could
   silently write the wrong DB.)
2. **SES_Brain write guardrail** — read-mostly vault; writes restricted to `raw\` + `projects\[name]\`.
   Recent write (`raw/2026-09-07_control_panel_drawing_house_rules_DRAFT.md`) landed in the ALLOWED zone,
   so no breach — but the rule needs enforcing/testing, not just stating.

## 8. Next actions (Monday)
1. Create AI Dev Work task (VPS/Webhooks section) — deploy portal.yml + static server + Cloudflare Access.
2. Confirm hostname + push cadence + Access IP range.
3. Implement D1/D2 guardrails (PATH_CONFIG `external:`/`ses_brain:` blocks + write-guard rule).
4. Execute deployment with verification (public 200 + Access challenge + idempotent push).
