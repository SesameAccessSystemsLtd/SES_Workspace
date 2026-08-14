#!/bin/bash
# start_dispatch_server.sh — launch the BOM tracker dispatch server (host, MSYS).
# Idempotent: bom_dispatch_server.py exits cleanly if :8765 is already bound,
# so running this repeatedly (boot hook / Hermes service / manual) is safe.
PY="C:/Users/GilesHugo/SES_Workspace/.venv/Scripts/python.exe"
SRV="C:/Users/GilesHugo/SES_Workspace/skills/os_portal/bom_dispatch_server.py"
LOG="C:/Users/GilesHugo/SES_Workspace/run_history/bom_dispatch_server.log"
nohup "$PY" "$SRV" >> "$LOG" 2>&1 &
echo "bom_dispatch_server launched (pid $!) — health: http://127.0.0.1:8765/health"
