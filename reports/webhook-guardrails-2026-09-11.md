# Webhook Guardrails — Status & How to Validate the NO-GO
Date: 2026-09-11  |  Deployed: YES (VPS)  |  Validated: YES

## What is in place

Seven independent layers, ALL required before any Asana write can occur.
Deployed in /opt/ses/webhook-receiver/receiver.py as `write_gate()`.

| Layer | Control | Current state | Blocks when |
|---|---|---|---|
| L1 STRUCTURAL | No Asana credential + no HTTP client in the service env | no credential present | always (cannot write at all) |
| L2 ENV FLAG | RECV_WRITE_ENABLED must be "1" | unset | flag off |
| L3 CONFIG | rules.json "mode" must be "auto" | "detect" | mode not auto |
| L4 APPROVAL | file WRITE_APPROVED containing the exact phrase | absent | file missing / phrase mismatch |
| L5 KILL SWITCH | file WRITE_HALT (any content) | absent | file present |
| L6 AUDIT | every decision recorded before action | active | (never blocks; observes) |
| L7 SCOPE LOCK | task GID must be on scope_allowlist.txt | EMPTY | list empty / GID not listed |

Deny-by-default: L7 starts empty, so even if every other layer were satisfied,
nothing is in scope and every write is refused.

## Validation evidence (run on the VPS 2026-09-11)
`cd /opt/ses/webhook-receiver && python3 test_guardrails.py`
  9 tests, 9 passed, 0 failed — each layer proven to block independently,
  plus one test proving the gate CAN pass when all conditions are met.

Live proof (real event through the public endpoint):
  MATCH (detect, no write) rule(s)=['inhouse-stock-action'] value='Valve INHOUSE'
  WRITE-GATE BLOCK: RECV_WRITE_ENABLED != 1 (env flag off)
  Decision recorded: webhook_gate_decisions{allowed:0, reason:"RECV_WRITE_ENABLED != 1"}

Production table `asana_write_log` deliberately NOT used — it belongs to another
system and has a different schema. Webhook decisions go to `webhook_gate_decisions`.

## How to validate the NO-GO over the coming days
1. Let DETECT run. Real Asana events accumulate in webhook_events.
2. Query what WOULD have been written:
     SELECT event_gid, target_gid, rule_ids, allowed, reason
       FROM webhook_gate_decisions ORDER BY id DESC;
   Every row should be allowed=0.
3. Confirm the production table is untouched:
     SELECT COUNT(*) FROM asana_write_log;   -- stays at 16 (pre-existing)
4. Review the matches for FALSE POSITIVES — this is the real test of the NO-GO:
     SELECT change_field, matched_rule, note, received_at
       FROM webhook_events WHERE matched_rule IS NOT NULL;
   Look for names containing INHOUSE that should NOT trigger (e.g. "NOT INHOUSE",
   "REVERT INHOUSE", supplier names). Each one is evidence for the matcher fix.

## NOT implemented (deliberately)
- The Asana write function itself. There is NO code that writes to Asana.
- X-Hook-Signature HMAC verification. REQUIRED before AUTO is ever considered.

## To (eventually) enable AUTO — five deliberate acts, in order
1. Add signature verification (X-Hook-Signature) — do this FIRST.
2. Add the write function + credential to the service environment.
3. Set RECV_WRITE_ENABLED=1 in the systemd unit.
4. Set rules.json "mode": "auto".
5. Create WRITE_APPROVED with the exact phrase, and populate scope_allowlist.txt.
Then: prove wrong-write rollback works before trusting it on a live project.

## Instant stop
  touch /opt/ses/webhook-receiver/WRITE_HALT     # stops writes (no restart)
  rm /opt/ses/webhook-receiver/WRITE_APPROVED    # also stops writes
  systemctl stop sas-webhook-receiver            # stops everything
