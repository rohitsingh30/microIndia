#!/bin/bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
UID_VALUE="$(id -u)"
mkdir -p "$ROOT/run" "$HOME/Library/LaunchAgents"

# Two supervisors: the collection plane (Chrome, sourcer, scraper, api, watchdog) and the
# insight plane (analyzer, backup). Each can be restarted without touching the other.
install_plane() {
  local LABEL="$1" CONFIG="$ROOT/run/$2.json" EXAMPLE="$ROOT/run/$2.example.json" STATE="$ROOT/run/$3-state.json" LOCK="$ROOT/run/$3.lock"
  local PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
  if [[ ! -f "$CONFIG" ]]; then
    cp "$EXAMPLE" "$CONFIG"
  fi
  local TMP="$PLIST.tmp"
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
    <string>$STATE</string>
    <string>--lock</string>
    <string>$LOCK</string>
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
  <string>$ROOT/run/$3.launch.out.log</string>
  <key>StandardErrorPath</key>
  <string>$ROOT/run/$3.launch.err.log</string>
</dict>
</plist>
EOF
  mv "$TMP" "$PLIST"
  launchctl bootout "gui/$UID_VALUE/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_VALUE" "$PLIST"
  launchctl kickstart -k "gui/$UID_VALUE/$LABEL"
  printf 'Installed and started %s (config %s, state %s)\n' "$LABEL" "$CONFIG" "$STATE"
}

install_plane com.microindia.scraper local-workers local-supervisor
install_plane com.microindia.insights insight-workers insight-supervisor
