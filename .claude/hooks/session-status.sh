#!/bin/sh
# SessionStart: show live microIndia status so every session starts from the real state.
ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"
cd "$ROOT/apps/scraper" 2>/dev/null || exit 0
echo "== microIndia status (read CLAUDE.md; non-trivial work goes through /plan-change) =="
PYTHONPATH=src .venv/bin/python -m microindia_scraper.status 2>&1 | head -12
exit 0
