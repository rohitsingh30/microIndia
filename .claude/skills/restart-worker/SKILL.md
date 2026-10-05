---
name: restart-worker
description: Safely restart one microIndia worker (scraper, sourcer, analyzer, api, watchdog, backup) so it picks up new code or recovers from a stall, without stopping the rest of collection. Use instead of kill, pkill or restarting the supervisor.
argument-hint: <worker name>
---

# /restart-worker $ARGUMENTS

1. Never restart `chrome` or `local_supervisor` this way unless the user explicitly asks. Restarting Chrome drops the signed-in session for every worker.
2. Check the worker exists and is running:
   ```bash
   cd /Users/rohit/projects/microIndia/apps/scraper && PYTHONPATH=src .venv/bin/python -m microindia_scraper.local_supervisor --state run/local-supervisor-state.json --status
   ```
3. Signal it the same way the watchdog does. The supervisor restarts it within its cooldown:
   ```bash
   PYTHONPATH=src .venv/bin/python -c "from microindia_scraper.watchdog import restart_worker; print(restart_worker('run/local-supervisor-state.json', '$ARGUMENTS'))"
   ```
   The command prints the old PID, or `None` if the worker wasn't running.
4. After about 20 seconds, run `/status` and confirm the worker has a new PID, a fresh heartbeat, and progress on its task kinds. For the scraper or sourcer, also check `tail -n 5 run/<worker>.log` for `runner_start`.
5. A **new** worker that isn't in the running supervisor yet can't be started this way. It needs the supervisor to reload its config; see `apps/scraper/CLAUDE.md`.
