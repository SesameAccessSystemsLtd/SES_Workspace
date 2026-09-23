#!/usr/bin/env python3
"""History scrubber run by `git filter-branch --tree-filter` over every commit.

Two jobs, both value-agnostic (no secret is printed or hardcoded here):
  1. Delete the accidentally-tracked password file from history.
  2. Redact literal values on secret-named fields in the invoice-processor
     config loader (ASANA_PAT, GRAPH_CLIENT_SECRET, and any other
     SECRET/KEY/TOKEN/PAT/PASSWORD field), replacing the value with a marker.

Non-secret fields (GUID tenant/client IDs, regex patterns, paths) are left alone.
"""
import os
import re
import sys

# 1) Remove the accidentally-tracked password file from history entirely.
pw = os.path.join("plans", "_vps_sasops_pw.txt")
if os.path.exists(pw):
    try:
        os.remove(pw)
    except OSError:
        pass

# 2) Redact secret-named fields in the config loader.
target = os.path.join("skills", "invoice-processor", "config-loader.ps1")
if os.path.exists(target):
    with open(target, "r", encoding="utf-8", errors="ignore") as fh:
        txt = fh.read()

    pattern = re.compile(
        r'([A-Za-z_.]*?(?:SECRET|_PAT\b|PAT$|TOKEN|PASSWORD|API_KEY|ACCESS_KEY)[A-Za-z0-9_]*'
        r'\s*=\s*)(["\'])([^"\']{6,})(["\'])',
        re.IGNORECASE,
    )

    def _redact(m):
        return m.group(1) + m.group(2) + "***REMOVED***" + m.group(4)

    new = pattern.sub(_redact, txt)

    if new != txt:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(new)

sys.exit(0)
