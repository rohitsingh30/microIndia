#!/bin/sh
# PreToolUse(Bash): refuse commands that would stop collection or destroy the live database.
cmd=$(jq -r '.tool_input.command // ""')
deny() {
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}
printf '%s' "$cmd" | grep -Eq '(^|[;&|[:space:]])(rm|mv|truncate|>)[^;&|]*data/[^[:space:]]*\.sqlite3' \
  && deny "microIndia guard: never delete, move or overwrite data/*.sqlite3*; the live database is in use. Use additive migrations in code."
printf '%s' "$cmd" | grep -Eiq '(pkill|killall)[^;&|]*chrome' \
  && deny "microIndia guard: never kill Chrome; it holds the signed-in Instagram session for every worker. Use /restart-worker for a single worker."
printf '%s' "$cmd" | grep -Eq '(pkill|killall|kill)[^;&|]*local_supervisor' \
  && deny "microIndia guard: never kill local_supervisor; it keeps every worker alive. Use /restart-worker <name>."
exit 0
