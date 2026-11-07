#!/bin/zsh
set -euo pipefail
cd "${0:A:h}/.."
project="$PWD/Unreal/TheHumanTouch"
state_dir="$project/Saved/DreamDebugger"
mkdir -p "$state_dir/worker"
if [[ ! -f "$state_dir/initial-state.json" ]]; then
  echo '{"q_left":[0,0,0,0,0,0],"q_right":[0,0,0,0,0,0],"gap_left_m":0.04,"gap_right_m":0.04}' > "$state_dir/initial-state.json"
fi
export DREAM_API_KEY="${DREAM_API_KEY:-op://wpt2zg5ue537o7tchkgxdj7ycm/2lituqqzteubyb2mb3wazr2lwy/credential}"
export DREAM_LOG=off
exec "$HOME/.codex/bin/op-agents" run -- uv run --with dreamlabs-client==1.1.3 --with dreamlabs==1.0.37 --with imageio-ffmpeg python -u DreamSim/worker.py --server "${DREAM_DEBUG_URL:-http://100.84.28.59:8786}" --token-file "$state_dir/worker-token" --project "$project" --data "$state_dir/worker" --initial-state "$state_dir/initial-state.json"
