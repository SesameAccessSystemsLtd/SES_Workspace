#!/bin/bash
TOKEN=*** '^ASANA_PERSONAL_ACCESS_TOKEN' /c/Users/GilesHugo/.Hermes/.env | head -1 | cut -d= -f2- | tr -d '"' | tr -d " \t\r\n")
API="https://app.asana.com/api/1.0"
PROJECT=1216293165459681
AUTH="Authorization: Bearer ${TOKEN}"

echo "=== Full raw project task list (paginated) ==="
cursor=""
while : ; do
  if [ -n "$cursor" ]; then
    resp=$(curl -s -G -H "$AUTH" --data-urlencode "opt_fields=name,resource_subtype,parent.name" "https://app.asana.com/api/1.0/projects/$PROJECT/tasks?limit=100&offset=$cursor")
  else
    resp=$(curl -s -G -H "$AUTH" --data-urlencode "opt_fields=name,resource_subtype,parent.name" "https://app.asana.com/api/1.0/projects/$PROJECT/tasks?limit=100")
  fi
  echo "--- page ---"
  echo "$resp" | grep -E '"(gid|name|resource_subtype|parent)"' | sed 's/^ *//'
  cursor=$(echo "$resp" | grep -o '"offset": *"[^"]*"' | head -1 | cut -d'"' -f4)
  has_more=$(echo "$resp" | grep -o '"has_more": *[a-z]*' | head -1 | cut -d'"' -f1-2)
  if echo "$resp" | grep -o '"has_more": *false' | grep -q false; then break; fi
  if [ -z "$cursor" ]; then break; fi
done

echo ""
echo "=== Parent chain for each of the 4 tasks ==="
for g in 1216293066712438 1216293066712442 1216293054994605 1216293066510705; do
  echo "--- $g ---"
  node="$g"
  for i in $(seq 1 12); do
    info=$(curl -s -G -H "$AUTH" --data-urlencode "opt_fields=name,parent.name" "https://app.asana.com/api/1.0/tasks/$node")
    nm=$(echo "$info" | grep -o '"name": *"[^"]*"' | head -1 | sed 's/.*"name": *"\([^"]*\)".*/\1/')
    par=$(echo "$info" | grep -o '"parent": *{[^}]*}' | sed 's/.*"gid": *"\([^"]*\)".*"name": *"\([^"]*\)".*/\1|\2/')
    echo "  $node | $nm"
    if [ -z "$par" ]; then echo "  (no parent / top-level)"; break; fi
    node=$(echo "$par" | cut -d'|' -f1)
  done
done
