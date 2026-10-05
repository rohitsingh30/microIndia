#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT_DIR"

BACKGROUND=0
if [ "${1:-}" = "--background" ]; then
  BACKGROUND=1
  shift
fi

SOURCE=${1:-${MICROINDIA_PUBLIC_CANDIDATE_SOURCE:-}}

if [ ! -x .venv/bin/python ]; then
  printf '%s\n' "Missing apps/scraper/.venv/bin/python; create the project virtualenv first." >&2
  exit 2
fi

# Sourcing never waits on a file: the sourcer runner keeps the India search grid
# queued and expands from every eligible creator. A username/NDJSON file, when
# given, is queued as one extra source.list task.
if [ -n "$SOURCE" ]; then
  case "$SOURCE" in
    /*) : ;;
    *) SOURCE="$ROOT_DIR/$SOURCE" ;;
  esac
  if [ -f "$SOURCE" ]; then
    PYTHONPATH="$ROOT_DIR/src" .venv/bin/python -m microindia_scraper.run add source.list "$SOURCE"
  else
    printf 'Seed list not found, ignoring: %s\n' "$SOURCE" >&2
  fi
fi
export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

# Insight plane (analyzer, backup) always runs in the background under its own supervisor,
# so it can be restarted without touching collection. A second start is refused by its lock.
[ -f run/insight-workers.json ] || cp run/insight-workers.example.json run/insight-workers.json
nohup .venv/bin/python -m microindia_scraper.local_supervisor \
  --config run/insight-workers.json \
  --state run/insight-supervisor-state.json \
  --lock run/insight-supervisor.lock \
  >>run/insight-supervisor.log 2>&1 </dev/null &
printf 'Started insight supervisor (pid %s).\n' "$!" >&2

[ -f run/local-workers.json ] || cp run/local-workers.example.json run/local-workers.json
printf '%s\n' "Starting collection supervisor (chrome, sourcer, scraper, api, watchdog)." >&2
if [ "$BACKGROUND" -eq 1 ]; then
  LOG_FILE=${MICROINDIA_SUPERVISOR_LOG:-run/public-collection-supervisor.log}
  nohup .venv/bin/python -m microindia_scraper.local_supervisor \
    --config run/local-workers.json \
    --state run/local-supervisor-state.json \
    --lock run/local-supervisor.lock \
    >"$LOG_FILE" 2>&1 </dev/null &
  printf 'Started collection supervisor (pid %s).\n' "$!"
  printf 'Log: %s\n' "$ROOT_DIR/$LOG_FILE"
  printf 'Status: PYTHONPATH=src .venv/bin/python -m microindia_scraper.local_supervisor --state run/local-supervisor-state.json --status\n'
else
  exec .venv/bin/python -m microindia_scraper.local_supervisor \
    --config run/local-workers.json \
    --state run/local-supervisor-state.json \
    --lock run/local-supervisor.lock
fi
