
import os, sys, subprocess, shutil
PROJ="vps_hermes"; CFG="prd"; ENV="/opt/ses/.hermes/.env"; UID="10000"
apply = "--apply" in sys.argv
r = subprocess.run(["doppler","secrets","download","--project",PROJ,"--config",CFG,
                    "--format","env","--no-file"],capture_output=True,text=True)
if r.returncode!=0: print("DOPPLER ERR",r.stderr); sys.exit(1)
dop={}
for ln in r.stdout.splitlines():
    ln=ln.strip()
    if "=" not in ln or ln.startswith("#"): continue
    k,v=ln.split("=",1); v=v.strip().strip('"').strip()
    if k.startswith("DOPPLER_"): continue
    dop[k]=v

keep = ["DEEPSEEK_API_KEY", "JIBBLE_API_KEY", "JIBBLE_KEY_ID"]
if os.environ.get("ASANA") == "1":
    keep += ["ASANA_PAT_MCP_KEY", "ASANA_CLINET_ID"]

new={}; missing=[]
for k in keep:
    if k in dop: new[k]=dop[k]
    else: missing.append(k)
if "TELEGRAM_BOT_ID" in dop and "TELEGRAM_BOT_API" in dop:
    new["TELEGRAM_BOT_TOKEN"]=dop["TELEGRAM_BOT_ID"]+":"+dop["TELEGRAM_BOT_API"]
else:
    missing.append("TELEGRAM_BOT_ID/BOT_API")
new["TELEGRAM_CHAT_ID"]="8551261362"; new["TELEGRAM_ALLOWED_USERS"]="8551261362"
if os.path.exists(ENV):
    for ln in open(ENV):
        if ln.startswith("API_SERVER_KEY"+"="): new["API_SERVER_KEY"]=ln.split("=",1)[1].strip()

if "TELEGRAM_BOT_TOKEN" in new and (len(new["TELEGRAM_BOT_TOKEN"])!=46 or ":" not in new["TELEGRAM_BOT_TOKEN"]):
    print("TELEGRAM SHAPE BAD",len(new["TELEGRAM_BOT_TOKEN"])); sys.exit(1)

print("== reconciled (names+lengths; values hidden) ==")
for k,v in sorted(new.items()): print("  %-28s len=%s"%(k,len(v)))
if missing: print("  MISSING from doppler (not added):", missing)
if os.path.exists(ENV):
    live={}
    for ln in open(ENV):
        if "=" in ln and not ln.startswith("#"):
            k,v=ln.split("=",1); live[k.strip()]=v.strip()
    added=[k for k in new if k not in live]
    changed=[k for k in new if k in live and new[k]!=live[k]]
    removed=[k for k in live if k not in new and k not in ("API_SERVER_KEY",)]
    print("== delta vs live (no values) ==")
    if added: print("  ADDED   :", sorted(added))
    if changed: print("  CHANGED :", sorted(changed))
    if removed: print("  REMOVED (would drop):", sorted(removed))
    if not (added or changed or removed): print("  (no change - env already current)")
if not apply:
    print("DRY-RUN: nothing written. --apply to commit."); sys.exit(0)

tmp=ENV+".tmp"
with open(tmp,"w") as f:
    order=["DEEPSEEK_API_KEY","TELEGRAM_BOT_TOKEN","TELEGRAM_CHAT_ID","TELEGRAM_ALLOWED_USERS","API_SERVER_KEY","JIBBLE_API_KEY","JIBBLE_KEY_ID","ASANA_PAT_MCP_KEY","ASANA_CLINET_ID"]
    for k in order:
        if k in new: f.write("%s=%s\n"%(k,new[k]))
subprocess.run(["chown",UID+":"+UID,tmp],check=True); subprocess.run(["chmod","600",tmp],check=True)
shutil.move(tmp,ENV)
print("== wrote",ENV,"; restarting =="); subprocess.run(["docker","restart","hermes-gateway"],check=True)
print("restarted.")
