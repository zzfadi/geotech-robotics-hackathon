#!/bin/zsh
# Install (or reinstall) the Table 10 login agent: link + live camera + headless real-arm service.
#   zsh DreamSim/install_table10_agent.sh          install / restart
#   zsh DreamSim/install_table10_agent.sh remove   stop and uninstall
set -euo pipefail
label=one.geotech.table10-agent
src="${0:A:h}/launchd/$label.plist"
dst="$HOME/Library/LaunchAgents/$label.plist"
mkdir -p "$HOME/Library/Logs/CoPresence"
launchctl bootout "gui/$UID/$label" 2>/dev/null || true
# A hand-started agent would fight the managed one over the heartbeat and the test commands.
pkill -f 'DreamSim/table10_agent.py' 2>/dev/null || true
if [[ "${1:-}" == "remove" ]]; then rm -f "$dst"; echo "removed $label"; exit 0; fi
plutil -lint "$src" >/dev/null
cp "$src" "$dst"
launchctl bootstrap "gui/$UID" "$dst"
echo "installed $label (log: ~/Library/Logs/CoPresence/$label.log)"
