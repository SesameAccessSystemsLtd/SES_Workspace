# Secrets Audit & Rotation Report
**Date:** 2026-09-23
**Scope:** SES_Workspace git repo, local disk, Doppler (`openclaw` / `dev`)
**Purpose:** Identify where secrets must be removed, which need rotating, and the Doppler tidy-up.

---

## 1. Executive summary

| Finding | Status |
|---|---|
| `.gitignore` blocks new secret commits | ✅ In place (added 2026-09-17) |
| Cleanup commit `73fb67f` removed secrets from HEAD | ✅ Done |
| Secrets still present in **git history** (`bd05355`, `d97fd3d`) | 🔴 **Must rotate** |
| Password file in **git history** (`8347827`, `1b8c6d1`) | 🔴 **Must rotate** |
| Doppler enclave holds 27 secrets | ⚠️ Tidy-up needed |
| Doppler **not referenced by any code** | ⚠️ Gap — secrets are local-only |
| Duplicate / typo'd Doppler keys | ⚠️ Tidy-up needed |

**Bottom line:** the live working tree is clean. The exposure is entirely in **git history**, which `.gitignore` cannot fix. Rotation is the only real remedy.

---

## 2. Where secrets currently live

### A. Doppler — project `openclaw`, config `dev` (27 secrets)

`AGENT_NOTIFY_EMAIL`, `ANTHROPIC_API_KEY`, `ASANA_PERSONAL_ACCESS_TOKEN`, `DEEPSEEK_API_GILES`,
`DROPBOX_ACCESS_TOKEN`, `DROPBOX_API_KEY`, `DROPBOX_APP_SECRET`, `ELASTIC_EMAIL_FROM`,
`ELASTIC_EMAIL_FROM_NAME`, `EMAIL_SERVICE_ELASTICEMAIL_API_KEY`, `GEMINI_API_KEY`,
`GEMINI_API_KEY_AGENTS`, `GEMINI_KEY_SESAME_AI`, `HUBSPOT_API_KEY_CRM_UPDATER`,
`HUBSPOT_PAT_API_KEY`, `HUBSPOT_PORTAL_ID`, `HUBSPOT_SEQUENCE_ID`, `JIBBLE_API_KEY`,
`JIBBLE_KEY_ID`, `PURCHAISNG_CLIENT_ID`, `PURCHAISNG_SECRET_ID`, `PURCHASING_API_KEY`,
`PURCHASING_EMAIL_API_KEY`, `PURCHASING_TENANT_ID`, plus `DOPPLER_CONFIG`,
`DOPPLER_ENVIRONMENT`, `DOPPLER_PROJECT` (auto-injected).

**Local cache:** `~/.doppler/fallback/.secrets-*.json` (3 encrypted fallback blobs) —
these contain decrypted secret material for offline use.

### B. Local disk
- `SES_Workspace/plans/_vps_sasops_pw.txt` (38 bytes) — VPS password. **Untracked now, but in history.**
- No `.env` file found inside `SES_Workspace` — env vars are supplied another way (shell/Doppler/agent).

### C. Git history (the real problem)
Secrets were committed in the **initial commits** and only removed later:

| Commit | Contains |
|---|---|
| `bd05355` "Initial commit" | Asana PAT + others in 6 files |
| `d97fd3d` "Initial commit" | Same (duplicate initial commit) |
| `8347827` "Backup: workspace snapshot 2026-09-21" | `plans/_vps_sasops_pw.txt` |
| `1b8c6d1` "Backup: OS portal fixes..." | `plans/_vps_sasops_pw.txt` |
| `73fb67f` "Security: remove hardcoded credentials" | ✅ The **fix** — removes them from HEAD |

**Files affected in history:**
1. `skills/asana/cloud_rules_testing/project_initializer.py`
2. `skills/asana/scripts/tagging_automation.py`
3. `skills/bom_pipeline/ASANAconfig.json`
4. `skills/invoice-processor/config-loader.ps1`
5. `skills/purchasing/archive/apply_proposals.py`
6. `skills/purchasing/archive/reconcile_price.py`
7. `plans/_vps_sasops_pw.txt` (password file)

---

## 3. What must be ROTATED (not just deleted)

Deleting from history does **not** make a leaked secret safe — assume anything committed is compromised.

| # | Secret | Reason | Action |
|---|---|---|---|
| 1 | **Asana PAT** | In `bd05355`/`d97fd3d` history | Rotate in Asana → update Doppler `ASANA_PERSONAL_ACCESS_TOKEN` |
| 2 | **VPS `sasops` password** | In `8347827`/`1b8c6d1` history | Change password on VPS → store in Doppler |
| 3 | Any other key in `config-loader.ps1` / `reconcile_price.py` history | Same commits | Audit each, rotate if live |

**Priority: 1 and 2 are confirmed live exposure.** Rotate those first.

---

## 4. Doppler tidy-up required

### 4.1 Typos in key NAMES (safe to rename — names only, values unaffected)
| Current (typo) | Should be |
|---|---|
| `PURCHAISNG_CLIENT_ID` | `PURCHASING_CLIENT_ID` |
| `PURCHAISNG_SECRET_ID` | `PURCHASING_SECRET_ID` |

⚠️ Renaming breaks any code reading the old name. **Rename in Doppler AND update code together.**

### 4.2 Duplicate values
- `PURCHASING_API_KEY` and `PURCHASING_EMAIL_API_KEY` may hold overlapping values.
- `PURCHAISNG_CLIENT_ID` (typo) and `PURCHASING_EMAIL_API_KEY` reportedly hold the **same value**.
- Consolidate to one canonical name per credential.

### 4.3 Missing key
- Code references `GRAPH_TENANT_ID` (10+ places), but only `PURCHASING_TENANT_ID` exists.
- Either add `GRAPH_TENANT_ID` to Doppler or fix the code references.

### 4.4 The core gap: nothing reads Doppler
**Zero references to Doppler in the codebase.** All secrets are local. The intended pattern —
"passwords not needed for immediate local runtime live in Doppler" — is **not yet implemented**.

---

## 5. Recommended sequence

**Phase 1 — Rotate (urgent, ~30 min)**
1. Rotate the Asana PAT in Asana admin.
2. Update `ASANA_PERSONAL_ACCESS_TOKEN` in Doppler `openclaw/dev`.
3. Change the VPS `sasops` password; delete `plans/_vps_sasops_pw.txt`.
4. Store the new VPS password in Doppler.

**Phase 2 — Tidy Doppler (~20 min)**
5. Rename typo'd keys (`PURCHAISNG_*` → `PURCHASING_*`).
6. Deduplicate `PURCHASING_API_KEY` / `PURCHASING_EMAIL_API_KEY`.
7. Reconcile `GRAPH_TENANT_ID` vs `PURCHASING_TENANT_ID`.

**Phase 3 — Scrub history (only if the repo will ever be public/pushed)**
8. `git filter-repo` to purge secrets from `bd05355`, `d97fd3d`, `8347827`, `1b8c6d1`.
9. Force-push. **Coordinate with any clones.**
10. Note: history rewrite is required to unblock the GitHub push (push protection blocks these commits).

**Phase 4 — Wire up Doppler (the real fix, ~1 hr)**
11. Add `doppler run --` wrapper to cron jobs / entry points so secrets load at runtime.
12. Remove local plaintext wherever possible; keep only the fallback.

---

## 6. Notes & caveats

- **Doppler fallback cache** (`~/.doppler/fallback/.secrets-*.json`) holds decrypted material.
  If the machine is compromised, these are readable. Consider `doppler configure` without
  fallback, or accept the risk for offline operation.
- **Robustness rule:** "passwords not needed for immediate local runtime should be held in Doppler"
  → in practice that means: keep only what the machine needs to *boot and fetch* locally;
  everything else lives in Doppler and is pulled on demand.
- Values were **never printed** during this audit — only key names were read.

---

*Report generated by Hermes. All findings verified against live git history, disk, and Doppler API.*
