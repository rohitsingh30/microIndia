---
name: restart-worker
description: Safely restart one microIndia worker (scraper, sourcer, analyzer, api, watchdog, backup) so it picks up new code or recovers from a stall, without stopping the rest of collection. Use instead of kill, pkill or restarting the supervisor.
argument-hint: <worker name>
---

# /restart-worker $ARGUMENTS

1. Never restart `chrome` or `local_supervisor` this way unless the user explicitly asks. Restarting Chrome drops the signed-in session for every worker.
2. Check the worker exists and is running. There are two supervisor planes: **collection** (chrome, sourcer, scraper, api, watchdog, keep-awake) and **insight** (analyzer, backup).
   ```bash
   cd /Users/rohit/projects/microIndia/apps/scraper && PYTHONPATH=src .venv/bin/python -m microindia_scraper.status --json | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin)['workers'], indent=1))"
   ```
3. Signal it the same way the watchdog does. Whichever supervisor owns it restarts it within its cooldown:
   ```bash
   PYTHONPATH=src .venv/bin/python -c "from microindia_scraper.watchdog import restart_worker; print(restart_worker(['run/local-supervisor-state.json', 'run/insight-supervisor-state.json'], '$ARGUMENTS'))"
   ```
   The command prints the old PID, or `None` if the worker wasn't running.
4. After about 20 seconds, run `/status` and confirm the worker has a new PID, a fresh heartbeat, and progress on its task kinds. For the scraper or sourcer, also check `tail -n 5 run/<worker>.log` for `runner_start`.
5. A **new** worker isn't picked up by a running supervisor, because supervisors read their config only at start. Add new non-collection workers to `run/insight-workers.json` (and the `.example`), then restart only the insight supervisor. Signal its `supervisor_pid` from `run/insight-supervisor-state.json` with `kill <pid>`, wait until that pid is gone, then start it directly:
   ```bash
   cd /Users/rohit/projects/microIndia/apps/scraper && PYTHONPATH=src nohup .venv/bin/python -m microindia_scraper.local_supervisor \
     --config run/insight-workers.json --state run/insight-supervisor-state.json --lock run/insight-supervisor.lock \
     >>run/insight-supervisor.log 2>&1 </dev/null &
   ```
   Don't use `start-public-collection.sh` for this: it truncates the collection supervisor's log and can queue a seed list. Never restart the collection supervisor for this.
