#!/bin/zsh
# Table 10 Dream runner for the VR cup handover. Same credential path as start_fleet.command:
# the Dream key is injected by the op-agents launcher and never handled by these scripts.
set -euo pipefail
cd "${0:A:h}/.."
if [[ -z "${DREAM_API_KEY:-}" ]]; then
  DREAM_API_KEY="$(grep -o 'op://[^"}]*' DreamSim/start_worker.command | head -1)"
  export DREAM_API_KEY
fi
export DREAM_LOG=off
exec "$HOME/.codex/bin/op-agents" run -- uv run --with dreamlabs-client==1.1.3 --with dreamlabs==1.0.37 --with imageio-ffmpeg --with scipy python -u DreamSim/table10_dream.py "$@"
