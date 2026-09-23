#!/usr/bin/env python3
"""
qa_server.py — RETIRED 2026-09-18.

The QA portal was a static snapshot keyed by sheet ROW NUMBER; because
05_sheet_approver.py deletes Pending rows every 30 minutes, a decision saved in
the portal could be pushed onto the wrong email. It also needed a hand-started
server and a manual `qa_sync.py --push`.

Review and feedback now live in the Email Gateway Google Sheet only:
  Pending   — DECISION (A / M / R / S=accept suggestion), Amendment Notes, Feedback
  Processed — full history, plus what the gateway learned from each decision
  Rules     — the learned rules (learn.py), human-readable and editable

qa_sync.py / qa_scorecard.py remain only for reading history; they no longer
write decisions. See SKILL.md.
"""
import sys

print(__doc__)
sys.exit(2)
