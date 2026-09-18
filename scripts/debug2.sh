#!/bin/bash
TOKEN=*** '^ASANA_PERSONAL_ACCESS_TOKEN' /c/Users/GilesHugo/.Hermes/.env | head -1 | cut -d= -f2- | tr -d '"' | tr -d " \t\r\n")
API="https://app.asana.com/api/1.0"
PROJECT=1216293165459681
AUTH=*** Bearer $TOKEN"

# ----- Debug the project task list -----
echo "########## FULL PROJECT TASK LIST (raw gid/name) ##########"
cursor=""
page=0
while : ; do
  page=$((page+1))
  if [ -n "$cursor" ]; then
    resp=$(curl -s -G -H "$AUTH" --data-urlencode "opt_fields=name" "https://app.asana.com/api/1.0/projects/$PROJECT/tasks?limit=100&offset=$cursor")
  else
    resp=$(curl -s -G -H "$AUTH" --data-urlencode "opt_fields=name" "https://app.asana.com/api/1.0/projects/$PROJECT/tasks?limit=100")
  fi
  echo "--- PAGE $page ---"
  echo "$resp" | grep -o '"gid": *"[0-9]*"' | head -100
  echo "--- names on this page ---"
  echo "$resp" | grep -o '"name": *"[^"]*"' | head -100
  cursor=$(echo "$resp" | grep -o '"offset": *"[^"]*"' | head -1 | cut -d'"' -f4)
  hasmore=$(echo "$resp" | grep -o '"has_more": *[a-z]*' | head -1 | cut -d' ' -f2)
  echo ">>> cursor=[$cursor] has_more=[$hasmore]"
  if [ "$hasmore" != "true" ] || [ -z "$cursor" ]; then break; fi
done

echo ""
echo "########## SUBTASKS OF 'Anodise wheel stop' 1217530130335073 ##########"
curl -s -G -H "$AUTH" --data-urlencode "opt_fields=name" "https://app.asana.com/api/1.0/tasks/1217530130335073/subtasks?limit=100"
echo ""
