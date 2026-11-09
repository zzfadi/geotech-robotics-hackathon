#!/bin/bash
# Dream's own robot station on the Jetson (orin-arm), driven from the Mac through its local API (127.0.0.1:8091).
#   DreamSim/station.sh up                 start the station (asks the Jetson sudo password if CAN links are down)
#   DreamSim/station.sh start ["prompt"]   run a prompt (default: Saleh's hand-to-hand bottle prompt) - ARMS MOVE
#   DreamSim/station.sh stop | home        hold where they are | move both arms home
#   DreamSim/station.sh status             state, plans, pauses, timing
#   DreamSim/station.sh down               stop the station; both arms go limp (hold them)
# Model: DREAM_CHECKPOINT (default dreamlabs/dream-1-beta). Setup files: arm/jetson-station/.
set -euo pipefail
HOST=${DREAM_STATION_HOST:-orin-arm}
DREAMCLI=/home/fadi/dream-station/.venv/bin/dreamcli
PROMPT="Use the right arm to take the bottle from my hand, place it in my other open hand, release it, and return home. Keep the left arm still."
CHECKPOINT=${DREAM_CHECKPOINT:-dreamlabs/dream-1-beta}

summary() {
  python3 -c "import json, sys
s = json.load(sys.stdin)
if 'error' in s and 'state' not in s: sys.exit('station: ' + s['error'])
t = s.get('timing') or {}
print('%s  run %s  %d s  plans %s  pauses %s  speed caps %s  round trip p50 %d ms  arms %s  error %s' % (
    s['state'], s['run'], round(s.get('run_elapsed_s') or 0), s['chunks'], s['stalls'], s['clamps'],
    round(t.get('p50_ms') or 0), s['arms'], s.get('last_error')))"
}
api() { ssh "$HOST" "curl -s -m 120 -X $1 http://127.0.0.1:8091/$2 -H 'Content-Type: application/json' --data-binary @-"; }

case "${1:-status}" in
  up) ssh -t "$HOST" "$DREAMCLI robot up --view" ;;
  down) ssh -t "$HOST" "$DREAMCLI robot down" ;;
  start)
    python3 -c 'import json, sys; print(json.dumps({"prompt": sys.argv[1], "checkpoint": sys.argv[2]}))' \
      "${2:-$PROMPT}" "$CHECKPOINT" | api POST start | summary ;;
  stop | home) echo '{}' | api POST "$1" | summary ;;
  status) ssh "$HOST" 'curl -s -m 10 http://127.0.0.1:8091/status' | summary ;;
  *) sed -n '2,8p' "$0"; exit 2 ;;
esac
