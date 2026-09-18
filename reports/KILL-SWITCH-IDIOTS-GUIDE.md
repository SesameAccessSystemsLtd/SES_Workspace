# Kill Switch — Idiot's Guide

**For:** Giles  **Date:** 11 September 2026  **Status:** LIVE (cutover 11 Sep 2026)

---

## READ THIS FIRST (the one thing you need to know)

**The kill switch is live and working from the link.**

It is built, tested and working, and as of 11 September 2026 the public address
`hooks.sesameaccess.com` serves the NEW receiver (v2), which has the kill switch.
Tested end to end: the link pauses and resumes correctly.

**But you almost certainly do not need it yet.** The receiver is in `detect` mode and
cannot write to Asana at all (see *Why you probably don't need it yet*). The kill
switch is your safety net for LATER, when the automation is armed.

---

## WHAT IS IT?

A single link you can click to **instantly stop the automation from writing to Asana.**

- Click it -> the system stops making changes.
- It **cannot** start the system. It can only stop it. That is deliberate.
- It cannot be used to make any change to Asana. It only presses "pause".

Think of it as a big red STOP button, not an on/off switch.

---

## WHEN WOULD YOU USE IT?

Only if the automation is **armed** (allowed to write to Asana) and you see it doing
something wrong — for example:

- A task gets flagged "INHOUSE" that shouldn't be
- You spot it changing parts in a way you didn't expect
- Anything that looks like a runaway loop, changing lots of tasks quickly

If it's not armed, there is nothing to stop — it cannot write anyway.

---

## WHERE IS IT?

The link lives on the server at:

```
/opt/ses/.hermes/sas-hooks/kill-switch-link.txt
```

It looks like this (the long code is your secret token):

```
https://hooks.sesameaccess.com/control/pause?token=YOUR-SECRET-CODE&confirm=1
```

**To get the actual link**, run this from your side:

```bash
ssh vps "cat /opt/ses/.hermes/sas-hooks/kill-switch-link.txt; echo"
```

That prints the full link with your code in it. Copy the whole line.

> **Note:** the code is a password. Anyone with the link can PAUSE the automation
> (they still cannot make it write). Keep it private — phone notes or a password
> manager, NOT a shared Asana task description.

---

## HOW TO USE IT

### The normal way (once it is live)

1. Open the link in a browser (phone is fine).
2. You'll see a **PAUSE NOW** button. Click it.
3. You'll get a confirmation page saying **PAUSED**.
4. Done. It takes effect **immediately** — measured at about **0.02 seconds**.

The `&confirm=1` on the end means it pauses on the first click. Without it, it shows
the button to confirm first — a safety step so link-scanners can't trigger it by accident.

### The bulletproof way (if the link is fiddly)

Paste this into any terminal — it cannot get mangled:

```bash
curl -s "https://hooks.sesameaccess.com/control/pause?token=PASTE-YOUR-CODE-HERE&confirm=1"
```

### To start it again (resume)

Resume is deliberately **not** a clickable link, so it cannot be triggered by accident:

```bash
curl -s -X POST "https://hooks.sesameaccess.com/control/resume?confirm=RESUME&token=PASTE-YOUR-CODE-HERE"
```

Resuming only puts it back to **detect** (watching, not writing). It **never** re-arms it.
Getting back to writing requires someone to deliberately arm it again.

---

## HOW LONG DOES IT TAKE?

| Action | Time |
|---|---|
| Pausing | **Instant** — about 0.02 seconds |
| Taking effect | Immediate, no delay |
| Resuming | Instant |
| Comes back after a crash | Up to **5 minutes** |

**The 5 minutes is about recovery, not the kill switch.** If the receiver crashes, a
watchdog job runs every 5 minutes and restarts it automatically. Worst case the receiver
is down 5 minutes. It comes back in `detect` mode and **unarmed** — it does not come back
with writes switched on.

---

## THE THREE STATES

| State | What it means | Can it write to Asana? |
|---|---|---|
| **detect** | Watching and logging only. CURRENT STATE. | **No** — never |
| **paused** | Kill switch pressed. | **No** |
| **armed** | Deliberately switched on to make changes. | Yes, within limits |

Right now it is in **detect**. Writes are impossible.

---

## WHY YOU PROBABLY DON'T NEED IT YET

The receiver in its current state **physically cannot write to Asana**. It:

- has no Asana password anywhere in it
- has no ability to make web requests at all
- is set to `detect` mode

To ever write, someone would have to deliberately do **five separate things** — add the
password, add the ability to call Asana, switch on a permission flag, change the mode,
and create an approval file. It cannot happen by accident or one wrong setting.

So the kill switch is preparation for later, not something to watch today.

---

## BEFORE IT BECOMES A REAL PANIC BUTTON

One job is left: point `hooks.sesameaccess.com` at the new receiver (v2). That is a
one-line routing change, deliberately not done yet — because the old receiver currently
has a queued recommendation not to be replaced until certain fixes are verified in.
Once that cutover happens, the link above starts working and this guide becomes live.

---

## WHERE TO GET MORE INFORMATION

**On the server (host side):**

| File | What it covers |
|---|---|
| `/opt/ses/webhook-receiver/KILLSWITCH-DOPPLER-SUPERVISION.md` | How the switch works, where the code lives, restart behaviour |
| `/opt/ses/webhook-receiver/GUARDRAILS.md` | The full list of safety layers, in detail |
| `/opt/ses/webhook-receiver/GUARDRAIL-STATUS.md` | Proof each layer works, and how to test them |
| `/opt/ses/webhook-receiver/AUDIT-2026-09-11.md` | The full risk audit and what still needs fixing |
| `/opt/ses/webhook-receiver/V1-V2-VERIFICATION.md` | Comparison of old vs new receiver |

Read them with:

```bash
ssh vps "cat /opt/ses/webhook-receiver/GUARDRAILS.md"
```

**In your own workspace:**

```
SES_Workspace\reports\killswitch-doppler-supervision-2026-09-11.md
SES_Workspace\reports\asana-webhook-audit-2026-09-11.md
SES_Workspace\reports\v1-v2-receiver-verification-2026-09-11.md
```

**To check its current state at any time:**

```bash
ssh vps "curl -s http://172.17.0.2:8099/health"
```

You'll see `"mode":"detect"`, `"armed":false`, `"paused":false`. If `"paused":true`,
the kill switch has been pressed.

**To ask me (Hermes):** just say "check the kill switch" or "is the webhook armed?" and
I'll report the live state.

---

## QUICK REFERENCE CARD

```
STOP IT:
  click the link, then PAUSE NOW
  (or) curl -s "https://hooks.sesameaccess.com/control/pause?token=CODE&confirm=1"

START IT AGAIN:
  curl -s -X POST "https://hooks.sesameaccess.com/control/resume?confirm=RESUME&token=CODE"

GET THE LINK:
  ssh vps "cat /opt/ses/.hermes/sas-hooks/kill-switch-link.txt; echo"

CHECK STATE:
  ssh vps "curl -s http://172.17.0.2:8099/health"

HOW FAST:            instant (0.02 seconds)
CAN IT START WRITES? No. Never. It can only pause.
WORKING TODAY?       Yes — live since 11 Sep 2026.
```

---

## HONEST SUMMARY

- The kill switch is **built, tested and works** — verified it pauses and resumes.
- It IS reachable: the public link works (verified 11 Sep 2026).
  the old receiver.
- Nothing can write to Asana right now anyway, so there is no risk in the meantime.
- When the cutover happens, this guide becomes live and the link works as described.
