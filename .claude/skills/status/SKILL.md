---
name: status
description: Show microIndia's live health in one screen covering workers, per-kind progress and stalls, queue, kept creators per hour, the insight backlog (reels fetched and analysed, dossiers, business cards) and open issues. Use at the start of any session, before restarting anything, and whenever someone asks "what's happening" or "is it running".
---

# /status

Read-only. Never restarts or changes anything.

1. Run:
   ```bash
   cd /Users/rohit/projects/microIndia/apps/scraper && PYTHONPATH=src .venv/bin/python -m microindia_scraper.status --json
   ```
2. If any `progress.<kind>.stalled` is true, or `issues` is non-empty, look at the evidence:
   - `tail -n 30 run/<worker>.log` for the worker that runs that kind (`scrape.*` → scraper, `source.*` → sourcer, `analyze.*`/`media.*` → analyzer or scraper),
   - the most common `last_error` for that kind:
     ```bash
     sqlite3 "file:data/microindia.sqlite3?mode=ro" "SELECT substr(last_error,1,90), COUNT(*) FROM tasks WHERE kind='<kind>' AND last_error IS NOT NULL AND updated_at > strftime('%s','now')-3600 GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
     ```
3. Reply with:
   - a 3–6 line summary: what's running, throughput per kind, insight backlog, issues with their likely cause,
   - the single next action if something is wrong (for example "restart scraper: 8 expired leases, no progress for 40 min" → `/restart-worker scraper`).

Keep it short. No tables unless asked.
