#!/bin/zsh
set -eu
cd "${0:A:h}"
capture_uv=$(command -v uv || true)
if [[ -z "$capture_uv" && -x "$HOME/.local/bin/uv" ]]; then capture_uv="$HOME/.local/bin/uv"; fi
if [[ -z "$capture_uv" ]]; then echo "Install uv (https://docs.astral.sh/uv/) and reopen this launcher."; exit 1; fi
model_dir="$HOME/Library/Caches/TheHumanTouch"
model="$model_dir/pose_landmarker_lite.task"
digest=59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a
mkdir -p "$model_dir"
if [[ ! -f "$model" ]] || ! echo "$digest  $model" | shasum -a 256 -c --status; then
    download=$(mktemp "$model_dir/model.XXXXXXXX")
    trap 'rm -f "$download"' EXIT
    curl -fL 'https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task' -o "$download"
    echo "$digest  $download" | shasum -a 256 -c --status
    mv "$download" "$model"
fi
echo "Webcam pose capture is starting. Allow Terminal to use the camera if prompted."
echo "For full-body capture, face camera 0 with your head, hands and feet in view. Press Control-C to stop."
exec "$capture_uv" run --script webcam.py --model "$model" --camera 0 "$@"
