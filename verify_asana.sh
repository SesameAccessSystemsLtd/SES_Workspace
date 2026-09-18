#!/bin/bash
# Read PAT from .Hermes/.env, strip quotes/whitespace. Never print the value.
TOKEN=$(grep '^ASANA_PERSONAL_ACCESS_TOKEN' /c/Users/GilesHugo/.Hermes/.env | head -1 | cut -d= -f2- | tr -d '"' | tr -d " \t\r\n")
if [ -z "$TOKEN" ]; then
  echo "ERROR: token not found or empty"; exit 1
fi

PROJECT=1216293165459681
API="https://app.asana.com/api/1.0"

# auth header file to avoid leaking token in process list
HEADER_SPEC="Authorization: Bearer $TOKEN"

# Collect all tasks in the subtree

declare -a all_gids=()
declare -a seeds=()
declare -A names=()
declare -A seen=()

get_page_gids() {
  echo "$1" | grep -o '"gid": *"[0-9]*"' | sed 's/.*"gid": *"\([0-9]*\)".*/\1/'
}

# ---------- Collect ALL tasks via recursion ----------

# Get the top-level project tasks (paginated)
echo "Fetching top-level project tasks..."
cur=""
while : ; do
  local_resp=""
  if [ -n "$cur" ]; then
    local_resp=$(curl -s -H "$HEADER_SPEC" "$API/projects/$PROJECT/tasks?limit=100&opt_fields=name&offset=$cur")
  else
    local_resp=$(curl -s -H "$HEADER_SPEC" "$API/projects/$PROJECT/tasks?limit=100&opt_fields=name")
  fi
  while IFS= read -r g; do
    [ -z "$g" ] && continue
    if [ -z "${seen[$g]}" ]; then
      seeds+=("$g")
    fi
  done < <(get_page_gids "$local_resp")
  cur=$(echo "$local_resp" | grep -o '"offset": *"[^"]*"' | head -1 | cut -d'"' -f4)
  hm=$(echo "$local_resp" | grep -o '"has_more": *[a-z]*' | head -1 | cut -d' ' -f2)
  if [ "$hm" != "true" ] || [ -z "$cur" ]; then break; fi
done
echo "Top-level project tasks: ${#seeds[@]}"

# Get subtask gids for a task (handles pagination up to limit=100)
get_subtasks() {
  local parent="$1"
  local cur=""
  while : ; do
    local resp
    if [ -n "$cur" ]; then
      resp=$(curl -s -H "$HEADER_SPEC" "$API/tasks/$parent/subtasks?limit=100&opt_fields=name&offset=$cur")
    else
      resp=$(curl -s -H "$HEADER_SPEC" "$API/tasks/$parent/subtasks?limit=100&opt_fields=name")
    fi
    local gids
    gids=$(get_page_gids "$resp")
    [ -n "$gids" ] && echo "$gids"
    cur=$(echo "$resp" | grep -o '"offset": *"[^"]*"' | head -1 | cut -d'"' -f4)
    local hm
    hm=$(echo "$resp" | grep -o '"has_more": *[a-z]*' | head -1 | cut -d' ' -f2)
    if [ "$hm" != "true" ] || [ -z "$cur" ]; then break; fi
  done
}

crawl() {
  local gid="$1"
  if [ -n "${seen[$gid]}" ]; then return; fi
  seen[$gid]=1
  all_gids+=("$gid")
  local child
  for child in $(get_subtasks "$gid"); do
    crawl "$child"
  done
}

echo "Crawling subtree of all top-level tasks..."
for t in "${seeds[@]}"; do
  crawl "$t"
done

echo "TOTAL tasks in full subtree: ${#all_gids[@]}"

# ---------- Fetch names for all gids ----------
echo "Fetching names for all ${#all_gids[@]} tasks..."
names=()
for gid in "${all_gids[@]}"; do
  nm=$(curl -s -H "$HEADER_SPEC" "$API/tasks/$gid?opt_fields=name" | grep -o '"name": *"[^"]*"' | head -1 | sed 's/^"name": *"//; s/"$//')
  names[$gid]="$nm"
done
echo "Name fetch complete"

# Case-insensitive placeholder check
echo "=== Checking for case-insensitive [project name] placeholders ==="
found=0
for gid in "${all_gids[@]}"; do
  name="${names[$gid]}"
  if echo "$name" | grep -qiE '\[[^]]*project[^]]*name[^]]*\]'; then
    found=$((found+1))
    echo "PLACEHOLDER FOUND: gid=$gid name='$name'"
  fi
done
echo "Tasks still containing placeholder (case-insensitive): $found"

echo ""
echo "=== Verifying the 4 named tasks ==="
for gid in 1216293066712438 1216293066712442 1216293054994605 1216293066510705; do
  name=$(curl -s -H "$HEADER_SPEC" "$API/tasks/$gid?opt_fields=name" | grep '"name"' | head -1 | sed 's/.*"name": *"\(.*\)",\?$/\1/')
  echo "gid=$gid => name='$name'"
done

echo ""
echo "=== Full task listing ==="
for gid in "${all_gids[@]}"; do
  echo "$gid | ${names[$gid]}"
done
