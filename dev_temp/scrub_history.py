#!/usr/bin/env python3
"""History scrubber for `git filter-branch --tree-filter` (pass 2).

Removes/redacts legacy hardcoded credentials from files that still carry them in
OLD COMMITS (the working tree is already clean). Value-agnostic: it matches by
field/shape, never printing or storing any secret.

Covered:
  * skills/invoice-processor/config-loader.ps1  -> secret-named fields
  * plans/_vps_sasops_pw.txt                    -> delete entirely
  * Asana PAT literals (shape 1/xxxx/yyyy:zzz) in the known carriers:
      skills/asana/cloud_rules_testing/project_initializer.py
      skills/asana/scripts/tagging_automation.py
      skills/bom_pipeline/ASANAconfig.json
      skills/purchasing/archive/apply_proposals.py
      skills/purchasing/archive/reconcile_price.py
"""
import os
import re
import sys

# 1) Delete the accidentally-tracked password file from history entirely.
pw = os.path.join("plans", "_vps_sasops_pw.txt")
if os.path.exists(pw):
    try:
        os.remove(pw)
    except OSError:
        pass

PAT_LITERAL = re.compile(r'\b[12]/[0-9]{6,}/[0-9]{6,}:[A-Za-z0-9]{8,}\b')


def scrub_file(path, patterns):
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            txt = fh.read()
    except OSError:
        return
    new = txt
    for pat, repl in patterns:
        new = pat.sub(repl, new)
    if new != txt:
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
        except OSError:
            pass


# 2) config-loader secret-named fields.
conf = os.path.join("skills", "invoice-processor", "config-loader.ps1")
field_pat = re.compile(
    r'([A-Za-z_.]*?(?:SECRET|_PAT\b|PAT$|TOKEN|PASSWORD|API_KEY|ACCESS_KEY)[A-Za-z0-9_]*'
    r'\s*=\s*)(["\'])([^"\']{6,})(["\'])',
    re.IGNORECASE,
)
scrub_file(conf, [(field_pat, lambda m: m.group(1) + m.group(2) + "***REMOVED***" + m.group(4))])

# 3) Asana PAT literals in the known carriers (redact just the token literal).
carriers = [
    os.path.join("skills", "asana", "cloud_rules_testing", "project_initializer.py"),
    os.path.join("skills", "asana", "scripts", "tagging_automation.py"),
    os.path.join("skills", "bom_pipeline", "ASANAconfig.json"),
    os.path.join("skills", "purchasing", "archive", "apply_proposals.py"),
    os.path.join("skills", "purchasing", "archive", "reconcile_price.py"),
]
for c in carriers:
    scrub_file(c, [(PAT_LITERAL, "***REMOVED_ASANA_PAT***")])

sys.exit(0)
