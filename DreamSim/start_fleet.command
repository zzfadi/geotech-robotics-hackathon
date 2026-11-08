#!/bin/zsh
# Parallel replica-table Dream trials in the live editor. Same credential path as start_worker.command.
set -euo pipefail
cd "${0:A:h}/.."
project="${HUMANTOUCH_PROJECT:-/Users/geo_dennis/Desktop/Projects/geotech-robotics-hackathon/geotech-robotics-hackathon/Unreal/TheHumanTouch}"
if [[ -z "${DREAM_API_KEY:-}" ]]; then
  DREAM_API_KEY="$(grep -o 'op://[^"}]*' DreamSim/start_worker.command | head -1)"
  export DREAM_API_KEY
fi
export DREAM_LOG=off
exec "$HOME/.codex/bin/op-agents" run -- uv run --with dreamlabs-client==1.1.3 --with dreamlabs==1.0.37 --with imageio-ffmpeg --with scipy python -u DreamSim/fleet_worker.py --project "$project" "$@"
