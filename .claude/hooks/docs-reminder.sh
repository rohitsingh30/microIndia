#!/bin/sh
# Stop: if project code changed but no docs did, ask once to update docs and the governing artifact.
input=$(cat)
[ "$(printf '%s' "$input" | jq -r '.stop_hook_active // false')" = "true" ] && exit 0
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT" 2>/dev/null || exit 0
changed=$(git status --porcelain 2>/dev/null | awk '{print $NF}')
[ -z "$changed" ] && exit 0
code=$(printf '%s\n' "$changed" | grep -E '^(apps/|\.claude/agents/|\.claude/skills/)' | grep -vE '(README|CLAUDE)\.md$' | head -1)
docs=$(printf '%s\n' "$changed" | grep -E '^docs/|README\.md$|CLAUDE\.md$' | head -1)
if [ -n "$code" ] && [ -z "$docs" ]; then
  jq -n '{decision:"block",reason:"microIndia: project code changed but no docs did. If this change affects architecture, product, workflow or headline numbers, update docs/ARCHITECTURE.md or docs/product/STORY.md and run /sync-governing. If it does not, say so in one line and stop."}'
fi
exit 0
