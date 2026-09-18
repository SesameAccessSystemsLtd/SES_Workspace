# v1 vs v2 Webhook Receiver — Verification Report
Date: 2026-09-11  |  Action taken: NONE (no cutover, nothing public changed)
Prepared by: Hermes

## Context
A handoff instruction (via Telegram) asked me to re-point hooks.sesameaccess.com
from the "old host-side receiver behind Caddy" to a new hardened v2 receiver running
inside the hermes container. I verified every claim before acting. Several were wrong,
and one step would have been a security regression. NOTHING WAS CHANGED.

## Claim-by-claim verification

| Claim in the instruction | Reality (verified) |
|---|---|
| "served by your Caddy" | NO Caddy on the box: no binary, no container, no Caddyfile. Traefik serves 80/443. |
| "old receiver accepts forged events" | FALSE today. v1 scores 9/9 on its guardrail suite. (Lack of signature verification was an audit FINDING, since hardened.) |
| "publish 127.0.0.1:18099 -> 8099" | UNNECESSARY. Host already reaches v2 at 172.17.0.2:8099 (verified http 200). |
| "26/26 guardrail checks pass" | FALSE as of today. v2's own drill reports 19/26, 7 failed. |
| "stop the old host-side receiver" | Declined: would replace a validated receiver with one whose kill switch is broken. |
| "expected: POST returns 401" | Public POST returns 200 (that is v1, still serving). |
| "expected: /control/state returns 401" | Public returns 404 (v1 has no /control endpoints). |
| "Hermes re-registers the webhook" | I am Hermes; the webhook is already registered and active (failure_count: None). Nothing needed. |

## v2's own drill — the decisive evidence
Ran `drill_webhook_receiver.py` (dry-run; verified it left nothing armed/changed):

  *** 19/26 guardrail checks passed ***
  FAILED: A1   armed state: mode=arm + ARM flag => armed=true
  FAILED: A5b  rule matched and reached the write gate
  FAILED: A7b  confirmation page (link-prefetch safe, no side effect)
  FAILED: A8   kill-switch link pauses immediately
  FAILED: A9   *** KILL SWITCH STOPS WRITES *** (reason=none)
  FAILED: A10b resume cannot set mode=arm via link
  FAILED: A11  audit readable with token

All 7 failures cluster in the control / kill-switch path.

## Root cause (found independently, then confirmed by the drill)
`SAS_CONTROL_TOKEN` is NOT loaded into the v2 process:
  - not present in the container's /opt/data/.env
  - not exported by /opt/data/sas-hooks/svc-run.sh
  - v2 log shows the regression: token_set=True at 12:07 -> token_set=False at 12:10

Consequence: every /control/* request returns 401, INCLUDING with the correct token.
So the kill-switch link does not function:
  public link (routes to v1)      -> 404
  v2 direct, VALID token          -> 401
The token file itself is fine (/opt/data/sas-hooks/kill-switch-token.txt, mode 600, 43 chars).

## What is genuinely GOOD about v2
- Signature verification is implemented CORRECTLY and fails closed:
    no secret stored  -> 503 (refuse everything)   [verified]
    bad signature     -> 401                       [verified via drill]
    good signature    -> 200 accepted              [verified via drill]
- Control endpoints refuse unauthenticated callers (401) [verified]
- Brute-force lockout works [drill PASS]
- Loop-risk rules refused at load [drill PASS]
- Rate cap + auto-pause works [drill PASS]
- Fail-closed when mode=arm but ARM flag missing [drill PASS]
- Draft duplicates suppressed [drill PASS]
- armed=false, paused=false, mode=detect, no ARM flag -> writes impossible

The safe-direction property holds: even a leaked link can only PAUSE or resume-to-detect,
never ARM. That design is sound. The problem is purely that the token is not loaded.

## Side-by-side

| | v1 (host, PUBLIC now) | v2 (container, not public) |
|---|---|---|
| Guardrail suite | 9/9 PASS | 19/26 (7 fail) |
| Signature verification | none | implemented (fail-closed) |
| Control / kill switch | no endpoints | present but INOPERATIVE |
| Fail-closed w/o secret | n/a | yes (503) |
| Location | /opt/ses/webhook-receiver | container /opt/data/sas-hooks |
| Public reach | Traefik -> 127.0.0.1:8088 | reachable at 172.17.0.2:8099 |

## Current live state (unchanged, safe)
- Public endpoint: v1, detect mode, 9/9 guardrails, performs no writes
- v2: running, unarmed, paused=false, detect, no ARM flag
- Webhook: active (gid 1218390996958336), failure_count None
- I made NO changes to Traefik, the container, or either receiver
- No port was published; no service was stopped; no Caddyfile touched (none exists)

## To unblock a future cutover (container-side, do NOT do from Hermes)
1. Export SAS_CONTROL_TOKEN into the v2 service env (svc-run.sh or /opt/data/.env),
   sourced from /opt/data/sas-hooks/kill-switch-token.txt. Do not print the value.
2. Restart v2. Confirm /health shows token_set=True and secret_known behaviour.
3. Re-run drill_webhook_receiver.py -> target 26/26, especially A9 (kill switch).
4. Prove the kill-switch link works end-to-end.
ONLY THEN consider re-pointing the public hostname. Cutover is a one-line Traefik
file-provider change (see vps-inbound-webhook-exposure skill), NOT a Caddy edit.

## Recommendation
HOLD the cutover. Do not replace a 9/9-validated receiver with one whose panic button
fails. Fix the token loading, re-run the drill to 26/26, then re-point.
