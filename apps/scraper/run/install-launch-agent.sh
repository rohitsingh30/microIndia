#!/bin/bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$ROOT/run/local-workers.json"
PLIST="$HOME/Library/LaunchAgents/com.microindia.scraper.plist"
LABEL="com.microindia.scraper"
UID_VALUE="$(id -u)"

mkdir -p "$ROOT/run" "$HOME/Library/LaunchAgents"
if [[ ! -f "$CONFIG" ]]; then
  cp "$ROOT/run/local-workers.example.json" "$CONFIG"
fi

TMP="$PLIST.tmp"
cat > "$TMP" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/.venv/bin/python</string>
    <string>-m</string>
    <string>microindia_scraper.local_supervisor</string>
    <string>--config</string>
    <string>$CONFIG</string>
    <string>--state</string>
    <string>$ROOT/run/local-supervisor-state.json</string>
    <string>--lock</string>
    <string>$ROOT/run/local-supervisor.lock</string>
    <string>--poll-seconds</string>
    <string>2</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONPATH</key>
    <string>$ROOT/src</string>
    <key>PATH</key>
    <string>$ROOT/.venv/bin:/usr/bin:/bin</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>$ROOT/run/launch-agent.out.log</string>
  <key>StandardErrorPath</key>
  <string>$ROOT/run/launch-agent.err.log</string>
</dict>
</plist>
EOF
mv "$TMP" "$PLIST"

launchctl bootout "gui/$UID_VALUE/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_VALUE" "$PLIST"
launchctl kickstart -k "gui/$UID_VALUE/$LABEL"

printf 'Installed and started %s\n' "$LABEL"
printf 'Config: %s\n' "$CONFIG"
printf 'Status: %s\n' "$ROOT/run/local-supervisor-state.json"
