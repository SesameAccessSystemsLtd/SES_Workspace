#!/bin/bash
# Central helper: loads TOKEN, defines AUTH and API, provides functions.
TOKEN=$(grep '^ASANA_PERSONAL_ACCESS_TOKEN' /c/Users/GilesHugo/.Hermes/.env | head -1 | cut -d= -f2- | tr -d '"' | tr -d " \t\r\n")
API="https://app.asana.com/api/1.0"
PROJECT=1216293165459681

asana_get() {
  # asana_get "path" ["key=val" ...]
  local path="$1"; shift
  local args=(-s -G -H "Authorization: Bearer ***
  for a in "$@"; do args+=(--data-urlencode "$a"); done
  curl "${args[@]}" "$API/$path"
}

# Recursively crawl subtree, printing "GID<tab>NAME" for every task.
crawl_subtree() {
  local seen_file="$1"
  # print top-level project tasks
  local cursor=""
  while : ; do
    local resp
    if [ -n "$cursor" ]; then
      resp=$(asana_get "projects/$PROJECT/tasks?limit=100&opt_fields=name,resource_subtype&offset=$cursor")
    else
      resp=$(asana_get "projects/$PROJECT/tasks?limit=100&opt_fields=name,resource_subtype")
    fi
    echo "$resp"
    cursor=$(echo "$resp" | grep -o '"offset": *"[^"]*"' | head -1 | cut -d'"' -f4)
    has_more=$(echo "$resp" | grep -o '"has_more": *\(true\|false\)' | head -1 | cut -d'"' -f2)
    if [ "$has_more" != "true" ] || [ -z "$cursor" ]; then break; fi
  done
}
