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
printf '%s\n' "Starting collection supervisor (chrome, sourcer, scraper, dashboard)." >&2

export PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
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
