#!/usr/bin/env python3
"""
qa_server.py — tiny local server for the Live QA Action Portal.

Why: a browser page opened via file:/// cannot write notes/decisions back to
data/qa/qa_actions.json (no write access + no secure context for the File
System Access API). This server serves the portal over http://127.0.0.1:8611/
(localhost = secure context) and exposes POST /save so the page can persist
actions to data/qa/qa_actions.json. qa_sync.py --push then writes those to the
sheet.

Run:
    python qa_server.py            # serve portal + save endpoint
    python qa_server.py --port 8611
Browse: http://127.0.0.1:8611/qa_portal.html   (or the root redirects there)

Stdlib only — no new dependencies. Single-process; fine for one user.
AUTHOR: Hermes (email processing). Built 2026-08-13.
"""

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

try:  # allow running from any cwd
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    HERE = os.getcwd()

DATA_DIR = r"C:\Users\GilesHugo\SES_Workspace\data\qa"
PORTAL_FILE = os.path.join(DATA_DIR, "qa_portal.html")
ACT_JSON = os.path.join(DATA_DIR, "qa_actions.json")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep quiet-ish
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html", "/qa_portal.html"):
            if not os.path.exists(PORTAL_FILE):
                self._send(503, "Portal not generated yet. Run: python qa_sync.py --portal")
                return
            with open(PORTAL_FILE, "rb") as f:
                self._send(200, f.read())
            return
        if path == "/health":
            self._send(200, "ok")
            return
        self._send(404, "not found")

    def do_POST(self):
        path = urlparse(self.path).path
        if path == "/save":
            try:
                ln = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(ln).decode("utf-8")
                actions = json.loads(raw)
            except Exception as e:
                self._send(400, f"bad body: {e}")
                return
            try:
                with open(ACT_JSON, "w", encoding="utf-8") as f:
                    json.dump(actions, f, indent=2, ensure_ascii=False)
            except Exception as e:
                self._send(500, f"write failed: {e}")
                return
            n = len(actions) if isinstance(actions, (list, dict)) else 0
            self._send(200, json.dumps({"ok": True, "saved": n}),
                       ctype="application/json")
            return
        if path == "/push":
            # convenience: trigger sync to sheet in-process would be heavy;
            # instead return hint to run qa_sync.py --push separately.
            self._send(200, json.dumps({"ok": True, "hint": "run: python qa_sync.py --push"}),
                       ctype="application/json")
            return
        self._send(404, "not found")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8611)
    args = ap.parse_args()

    serve_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
    if not os.path.exists(PORTAL_FILE):
        print("WARNING: qa_portal.html not found — run qa_sync.py --portal first.")
    print(f"SAS OS QA portal: http://127.0.0.1:{args.port}/qa_portal.html")
    print("Save endpoint:    POST /save -> data/qa/qa_actions.json")
    print("(after saving in the page, run qa_sync.py --push to write to the sheet)")
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
