#!/bin/bash
# Dream's own robot station on the Jetson (orin-arm), driven from the Mac through its local API (127.0.0.1:8091).
#   DreamSim/station.sh demo [seconds]     THE DEMO: start the station if needed, run Saleh's prompt, show progress,
#                                          then stop and send both arms home after [seconds] (default 120) or Ctrl-C
#   DreamSim/station.sh up                 start the station (asks the Jetson sudo password if CAN links are down)
#   DreamSim/station.sh start ["prompt"]   run a prompt (default: Saleh's hand-to-hand bottle prompt) - ARMS MOVE
#   DreamSim/station.sh stop | home        hold where they are | move both arms home
#   DreamSim/station.sh status             state, plans, pauses, timing
#   DreamSim/station.sh down               stop the station; both arms go limp (hold them)
# Model: DREAM_CHECKPOINT (default dreamlabs/dream-1-beta). Host: DREAM_STATION_HOST (default orin-arm, an SSH alias).
# Setup files: arm/jetson-station/. Team access: docs/jetson-arms.md.
set -uo pipefail
HOST=${DREAM_STATION_HOST:-orin-arm}
DREAMCLI=/home/fadi/dream-station/.venv/bin/dreamcli
PROMPT="Use the right arm to take the bottle from my hand, place it in my other open hand, release it, and return home. Keep the left arm still."
CHECKPOINT=${DREAM_CHECKPOINT:-dreamlabs/dream-1-beta}

summary() {
  python3 -c "import json, sys
raw = sys.stdin.read().strip()
if not raw: sys.exit('station: not running (DreamSim/station.sh up starts it)')
s = json.loads(raw)
if 'error' in s and 'state' not in s: sys.exit('station: ' + s['error'])
t = s.get('timing') or {}
print('%s  run %s  %d s  plans %s  pauses %s  speed caps %s  round trip p50 %d ms  arms %s  error %s' % (
    s['state'], s['run'], round(s.get('run_elapsed_s') or 0), s['chunks'], s['stalls'], s['clamps'],
    round(t.get('p50_ms') or 0), s['arms'], s.get('last_error')))"
}
api() { ssh "$HOST" "curl -s -m 120 -X $1 http://127.0.0.1:8091/$2 -H 'Content-Type: application/json' --data-binary @-"; }
status_json() { ssh -o ConnectTimeout=5 "$HOST" 'curl -s -m 10 http://127.0.0.1:8091/status' 2>/dev/null; }
# The station's state word, or "down" when it is not running.
state() {
  status_json | python3 -c "import json, sys
raw = sys.stdin.read().strip()
print(json.loads(raw).get('state', 'down') if raw else 'down')" 2>/dev/null || echo down
}
start_json() {
  python3 -c 'import json, sys; print(json.dumps({"prompt": sys.argv[1], "checkpoint": sys.argv[2]}))' "$1" "$CHECKPOINT"
}

demo_finish() {
  trap - INT TERM
  echo; echo "stopping the run (arms hold), then sending both arms home..."
  echo '{}' | api POST stop | summary
  echo '{}' | api POST home | summary
  echo "done: both arms home and holding. 'DreamSim/station.sh down' lets them go limp."
  exit 0
}

demo() {
  local seconds=${1:-120}
  if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$HOST" true 2>/dev/null; then
    echo "cannot reach the Jetson ($HOST): join the travel router's Wi-Fi and check your SSH setup (docs/jetson-arms.md)"
    exit 1
  fi
  if [ "$(state)" = down ]; then
    echo "starting the station on the Jetson (type the Jetson password if it asks)..."
    ssh -t "$HOST" "$DREAMCLI robot up --view" || exit 1
    for _ in $(seq 1 60); do [ "$(state)" = idle ] && break; sleep 2; done
  fi
  case "$(state)" in
    idle) ;;
    running | starting)
      echo "a run is already going: stopping it and homing first"
      echo '{}' | api POST stop | summary; echo '{}' | api POST home | summary ;;
    *) echo "station is '$(state)', not idle:"; status_json | summary; exit 1 ;;
  esac
  echo "ARMS WILL MOVE: they go home, then Dream drives them. Hand on the E-stop, bottle in your hand."
  echo "Prompt: $PROMPT"
  for n in 3 2 1; do printf '  starting in %s... (Ctrl-C to cancel)\r' "$n"; sleep 1; done; echo
  trap demo_finish INT TERM
  start_json "$PROMPT" | api POST start | summary || demo_finish
  local t0=$SECONDS
  while [ $((SECONDS - t0)) -lt "$seconds" ]; do
    sleep 3
    line=$(status_json | summary 2>&1) || true
    echo "  $line"
    case "$line" in idle* | error* | *"not running"*) echo "the run ended by itself"; break ;; esac
  done
  demo_finish
}

case "${1:-status}" in
  demo) demo "${2:-120}" ;;
  up) ssh -t "$HOST" "$DREAMCLI robot up --view" ;;
  down) ssh -t "$HOST" "$DREAMCLI robot down" ;;
  start) start_json "${2:-$PROMPT}" | api POST start | summary ;;
  stop | home) echo '{}' | api POST "$1" | summary ;;
  status) status_json | summary ;;
  *) sed -n '2,11p' "$0"; exit 2 ;;
esac
