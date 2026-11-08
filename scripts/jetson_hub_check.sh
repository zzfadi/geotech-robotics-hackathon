#!/usr/bin/env bash
# Jetson hub check. Run from the Mac, from the repository root:
#
#   scripts/jetson_hub_check.sh                    # offline suites (about 20 s on the Orin Nano)
#   scripts/jetson_hub_check.sh --setup            # first time: build the venvs under ~/hub-tests (PyPI downloads)
#   scripts/jetson_hub_check.sh --sim              # + DreamSim, Capture and Unreal Tools model tests (venv-sim)
#   scripts/jetson_hub_check.sh --detector         # + still-image detector check (.pt CPU/CUDA, engines if built)
#   scripts/jetson_hub_check.sh --camera           # + a live C270 run (skipped if /dev/video0 is busy)
#   scripts/jetson_hub_check.sh --control-center   # + serve on the Jetson, tunnel, curl from the Mac, stop
#   scripts/jetson_hub_check.sh --all              # everything except --setup and --engines
#   scripts/jetson_hub_check.sh --engines          # build TensorRT FP16 engines (about 20 min, once per board)
#
# Environment: JETSON_HOST (default orin-arm), CAMERA_SECONDS (default 30), LOCAL_PORT (default 8780,
# the Mac end of the control center tunnel; pick another if 8780 is taken on the Mac).
#
# Safety: works only under ~/hub-tests on the Jetson. It never touches ~/geotech-robotics-hackathon,
# ~/jetson-runtime (its Python is only run, read-only, for the arm tests) or ~/jetson-prep-assets.
# No sudo, no system settings, no CAN/USB arm access, never starts pickup_server, no credentials.
# Ultralytics runs with YOLO_OFFLINE=1 and sync=False. Everything it starts ends before it exits.
# Exit status is 1 when any suite exits non-zero (with --sim, DreamSim does on Linux until the
# MakerArm provenance.json/PROVENANCE.json case bug is fixed).
set -u

HOST="${JETSON_HOST:-orin-arm}"
CAMERA_SECONDS="${CAMERA_SECONDS:-30}"
LOCAL_PORT="${LOCAL_PORT:-8780}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10)

want_setup=0 want_sim=0 want_detector=0 want_camera=0 want_cc=0 want_engines=0
for arg in "$@"; do
  case "$arg" in
    --setup) want_setup=1 ;;
    --sim) want_sim=1 ;;
    --detector) want_detector=1 ;;
    --camera) want_camera=1 ;;
    --control-center) want_cc=1 ;;
    --engines) want_engines=1 ;;
    --all) want_sim=1 want_detector=1 want_camera=1 want_cc=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown option: $arg (see --help)" >&2; exit 2 ;;
  esac
done

if ! "${SSH[@]}" "$HOST" true; then
  echo "cannot reach $HOST over ssh (BatchMode); check the Beryl LAN and ~/.ssh/config" >&2
  exit 1
fi

# Weights and still images the Mac check used (git-ignored on the Mac); copied once, if present.
if (( want_detector || want_camera || want_engines )); then
  "${SSH[@]}" "$HOST" 'mkdir -p ~/hub-tests/weights ~/hub-tests/images'
  w="$ROOT/vision/local/ultralytics/weights"
  i="$ROOT/vision/local/ultralytics/images"
  if [[ -f "$w/yolo26n.pt" && -f "$w/yolo26n-pose.pt" ]]; then
    scp -q "$w/yolo26n.pt" "$w/yolo26n-pose.pt" "$HOST:hub-tests/weights/"
  fi
  if compgen -G "$i/*.jpg" >/dev/null; then scp -q "$i"/*.jpg "$HOST:hub-tests/images/"; fi
fi

"${SSH[@]}" "$HOST" bash -s -- "$want_setup" "$want_sim" "$want_detector" "$want_camera" "$want_engines" "$CAMERA_SECONDS" <<'REMOTE'
set -u
want_setup=$1 want_sim=$2 want_detector=$3 want_camera=$4 want_engines=$5 camera_seconds=$6
H="$HOME/hub-tests"
REPO="$H/geotech-robotics-hackathon"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOGS="$H/logs/$STAMP"
mkdir -p "$LOGS" "$H/sessions" "$H/results" "$H/yolo-config"
export PYTHONDONTWRITEBYTECODE=1 YOLO_OFFLINE=1 YOLO_CONFIG_DIR="$H/yolo-config" UV_CACHE_DIR="$H/uv-cache"
ARM_PY=/home/fadi/jetson-runtime/.venv/bin/python
[[ -x "$ARM_PY" ]] && export ARMCTL_PYTHON="$ARM_PY"
UV="$(command -v uv || echo /home/fadi/jetson-runtime/unpack/uv-aarch64-unknown-linux-gnu/uv)"
SUMMARY=()
FAILED=0

echo "== host"
echo "  $(hostname)  $(uname -r)  $(head -1 /etc/nv_tegra_release | cut -d, -f1-2)  $(nvpmodel -q 2>/dev/null | head -1)"
echo "  python3 $(python3 -V 2>&1 | cut -d' ' -f2)  arm python ${ARMCTL_PYTHON:-missing}  uv $("$UV" --version 2>/dev/null | cut -d' ' -f2)"

echo "== checkout ($REPO)"
if [[ -d "$REPO/.git" ]]; then
  git -C "$REPO" fetch -q origin main && git -C "$REPO" merge -q --ff-only origin/main \
    || echo "  WARN: could not fast-forward the hub-tests checkout; testing what is there"
else
  git clone -q https://github.com/zzfadi/geotech-robotics-hackathon.git "$REPO"
fi
echo "  $(git -C "$REPO" log -1 --format='%h %cI %s')"
cd "$REPO" || exit 1

thermals() {  # one line: power mode, temperatures, fan, board input power
  local t="" z
  for z in /sys/class/thermal/thermal_zone*; do
    [[ -r "$z/temp" ]] && v=$(cat "$z/temp" 2>/dev/null) && t+="$(cat "$z/type" | sed 's/-thermal//')=$((v / 1000)).$(( (v % 1000) / 100 ))C "
  done
  local rpm pwm vin
  rpm=$(cat /sys/class/hwmon/hwmon*/rpm 2>/dev/null | head -1)
  pwm=$(for h in /sys/class/hwmon/hwmon*; do [[ "$(cat $h/name)" == pwmfan ]] && cat $h/pwm1; done 2>/dev/null)
  vin=$(timeout 3 tegrastats --interval 1000 2>/dev/null | head -1 | grep -o 'VDD_IN [0-9]*mW' )
  echo "  $1: $t fan_pwm=${pwm:-?}/255 fan_rpm=${rpm:-?} ${vin:-}"
}

suite() {  # suite NAME CMD...: run, time, count
  local name=$1; shift
  local log="$LOGS/$name.log" s e rc count
  s=$(date +%s.%N); "$@" >"$log" 2>&1; rc=$?; e=$(date +%s.%N)
  count=$(grep -oE '[0-9]+ (passed|failed|error)[^=]*in [0-9.]+s' "$log" | tail -1)
  [[ -z "$count" ]] && count=$(grep -E '^Ran [0-9]+ tests' "$log" | tail -1)" $(grep -E '^(OK|FAILED)' "$log" | tail -1)"
  [[ "$count" == " " ]] && count=$(grep -oE '"scenarios": [0-9]+, "passed": [0-9]+, "failed": [0-9]+' "$log" | tail -1)
  [[ -z "$count" || "$count" == " " ]] && count="(see log)"
  (( rc != 0 )) && FAILED=$((FAILED + 1))
  SUMMARY+=("$(printf '%-24s rc=%-3s %6.1fs  %s' "$name" "$rc" "$(echo "$e - $s" | bc)" "$count")")
}

if (( want_setup )); then
  echo "== setup (isolated venvs under $H; PyPI only)"
  [[ -d "$H/venv-vision" ]] || "$UV" venv -q --python /usr/bin/python3 "$H/venv-vision"
  "$UV" pip install -q --python "$H/venv-vision/bin/python" ultralytics opencv-python-headless tensorrt-cu13 onnx onnxslim
  [[ -d "$H/venv-trt10" ]] || "$UV" venv -q --python /usr/bin/python3 "$H/venv-trt10"
  "$UV" pip install -q --python "$H/venv-trt10/bin/python" ultralytics opencv-python-headless 'tensorrt-cu13>=10.15,<11' onnx onnxslim
  [[ -d "$H/venv-sim" ]] || "$UV" venv -q --python /usr/bin/python3 "$H/venv-sim"
  "$UV" pip install -q --python "$H/venv-sim/bin/python" numpy scipy mujoco opencv-python-headless pillow aiohttp pyarrow imageio-ffmpeg mediapipe pytest
  "$H/venv-vision/bin/yolo" settings sync=False >/dev/null 2>&1
  grep -q '"sync": false' "$H/yolo-config/Ultralytics/settings.json" && echo "  ultralytics sync=false"
fi

echo "== offline suites"
suite arm-test bash arm/tools/arm test -p no:cacheprovider
suite handoff-unit python3 -m unittest discover -s handoff -t . -p 'test_*.py'
suite handoff-replay python3 -m handoff.replay
suite control-center python3 -m unittest discover -s control_center/tests -t .
suite vision-unit python3 -m unittest discover -s vision -t . -p 'test_*.py'
suite vision-replay python3 -m vision.run --replay vision/examples/handoff.jsonl --target real
grep -q '"action": "start_handoff", "executor": "local"' "$LOGS/vision-replay.log" \
  && grep -q retreat_with_can "$LOGS/vision-replay.log" \
  && SUMMARY+=("  vision-replay: start_handoff executor=local and retreat_with_can present")

if (( want_sim )); then
  if [[ -x "$H/venv-sim/bin/python" ]]; then
    suite dreamsim "$H/venv-sim/bin/python" -m unittest discover -s DreamSim -p 'test_*.py'
    grep -q "MakerArm/provenance.json" "$LOGS/dreamsim.log" && SUMMARY+=("  dreamsim: errors read MakerArm/provenance.json; the tracked file is PROVENANCE.json (fine on macOS, fails on Linux)")
    suite capture "$H/venv-sim/bin/python" -m unittest discover -s Capture -p 'test_*.py'
    suite unreal-model "$H/venv-sim/bin/python" -m unittest discover -s Unreal/TheHumanTouch/Tools -p 'test_maker_arm_model.py'
    suite unreal-actions "$H/venv-sim/bin/python" -m unittest discover -s Unreal/TheHumanTouch/Tools -p 'test_maker_arm_actions.py'
  else
    SUMMARY+=("sim suites               skipped: no $H/venv-sim (run --setup)")
  fi
fi

VPY="$H/venv-vision/bin/python"
echo "== preflight (expect NO-GO or MARK-ONLY: no taught poses here)"
pf=(python3 -m handoff.preflight)
[[ -x "$VPY" ]] && pf+=(--vision-python "$VPY")
[[ -f "$H/weights/yolo26n.pt" && -f "$H/weights/yolo26n-pose.pt" ]] \
  && pf+=(--detect "$H/weights/yolo26n.pt" --pose "$H/weights/yolo26n-pose.pt")
"${pf[@]}" 2>&1 | sed 's/^/  /'

if (( want_engines )); then
  echo "== TensorRT FP16 engines (venv-trt10, TensorRT 10.x; 8 to 11 min per model)"
  mkdir -p "$H/engines-fp16" && cp "$H"/weights/yolo26n*.pt "$H/engines-fp16/"
  for m in yolo26n yolo26n-pose; do
    [[ -f "$H/engines-fp16/$m.engine" ]] && { echo "  $m.engine exists"; continue; }
    ( cd "$H/engines-fp16" && timeout 1200 "$H/venv-trt10/bin/yolo" export model=$m.pt format=engine quantize=16 device=0 ) \
      >"$LOGS/export-$m.log" 2>&1; echo "  $m rc=$? $(grep -o 'export success.*' "$LOGS/export-$m.log" | tail -1)"
  done
fi

still_summary() {  # still_summary JSON: median adapter ms per device over the images
  "$VPY" - "$1" <<'PY'
import json, statistics, sys
d = json.load(open(sys.argv[1]))
by = {}
for r in d["runs"]:
    by.setdefault(r["device"], []).append(r["adapter_ms"]["median"] if r["valid"] else None)
for dev, v in by.items():
    ok = [x for x in v if x is not None]
    print("  device %-4s images %d valid %d  adapter ms median of medians %s" % (dev, len(v), len(ok), statistics.median(ok) if ok else "-"))
PY
}

IMAGES=("$H"/images/*.jpg)
if (( want_detector || want_camera )); then thermals "before detector"; fi
if (( want_detector )); then
  echo "== detector, still images"
  if [[ -x "$VPY" && -f "$H/weights/yolo26n.pt" && -f "${IMAGES[0]}" ]]; then
    out="$H/results/still_check-$STAMP-pt.json"
    timeout 900 "$VPY" -m vision.still_check --detect "$H/weights/yolo26n.pt" --pose "$H/weights/yolo26n-pose.pt" \
      --device cpu --device 0 --repeat 10 "${IMAGES[@]}" --out "$out" >"$LOGS/still-pt.log" 2>&1 \
      && { echo "  .pt weights, $out"; still_summary "$out"; } || echo "  .pt still check failed: $LOGS/still-pt.log"
    if [[ -f "$H/engines-fp16/yolo26n.engine" && -f "$H/engines-fp16/yolo26n-pose.engine" ]]; then
      out="$H/results/still_check-$STAMP-fp16.json"
      timeout 900 "$H/venv-trt10/bin/python" -m vision.still_check \
        --detect "$H/engines-fp16/yolo26n.engine" --pose "$H/engines-fp16/yolo26n-pose.engine" --device 0 --repeat 10 \
        "${IMAGES[@]}" --out "$out" >"$LOGS/still-fp16.log" 2>&1 \
        && { echo "  FP16 engines, $out"; still_summary "$out"; } || echo "  engine still check failed: $LOGS/still-fp16.log"
    fi
  else
    echo "  skipped: needs $VPY (--setup), ~/hub-tests/weights/*.pt and ~/hub-tests/images/*.jpg"
  fi
fi

if (( want_camera )); then
  echo "== live C270 (/dev/video0), ${camera_seconds}s"
  busy=1
  for i in 1 2 3; do fuser /dev/video0 >/dev/null 2>&1 || { busy=0; break; }; echo "  camera busy (try $i)"; sleep 20; done
  if (( busy )); then
    echo "  skipped: /dev/video0 is in use"
  else
    if [[ -f "$H/engines-fp16/yolo26n.engine" && -f "$H/engines-fp16/yolo26n-pose.engine" ]]; then
      py="$H/venv-trt10/bin/python" det="$H/engines-fp16/yolo26n.engine" pose="$H/engines-fp16/yolo26n-pose.engine" tag=trt-fp16
    else
      py="$VPY" det="$H/weights/yolo26n.pt" pose="$H/weights/yolo26n-pose.pt" tag=pt-cuda
    fi
    timeout $(( camera_seconds + 90 )) "$py" -m vision.run --camera 0 --detect "$det" --pose "$pose" --device 0 --target real \
      --max-seconds "$camera_seconds" --record "$H/results/live-$STAMP-$tag.jsonl" --telemetry "$H/sessions" \
      --session "jetson-c270-$tag-$STAMP" >"$LOGS/live.log" 2>"$LOGS/live.stderr"
    echo "  $tag rc=$? $(grep session_summary "$LOGS/live.log" | tail -1)"
  fi
fi
if (( want_detector || want_camera )); then thermals "after detector"; fi

echo "== summary (logs in $LOGS)"
printf '  %s\n' "${SUMMARY[@]}"
(( FAILED == 0 )) || { echo "  $FAILED suite(s) exited non-zero"; exit 1; }
REMOTE
remote_rc=$?

if (( want_cc )); then
  echo "== control center on the Jetson through an ssh tunnel (Mac port $LOCAL_PORT)"
  if lsof -nP -iTCP:"$LOCAL_PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "  skipped: Mac port $LOCAL_PORT is in use; rerun with LOCAL_PORT=18780"
  elif "${SSH[@]}" "$HOST" 'ss -ltn | grep -q "127.0.0.1:8780 "'; then
    echo "  skipped: port 8780 is already in use on $HOST (someone else's control center?); left alone"
  else
    tmp="$(mktemp -d)" tunnel="" server=""
    cc_stop() {  # end the tunnel and this check's server; also runs on Ctrl-C
      [[ -n "$tunnel" ]] && kill "$tunnel" 2>/dev/null
      "${SSH[@]}" "$HOST" "pkill -u \$(id -un) -f '^python3 -m control_center --data /home/[^ ]*/hub-tests/cc-check serve'" 2>/dev/null
      [[ -n "$server" ]] && kill "$server" 2>/dev/null
      [[ -n "$tunnel" ]] && wait "$tunnel" 2>/dev/null
      [[ -n "$server" ]] && wait "$server" 2>/dev/null
      tunnel="" server=""
    }
    trap 'cc_stop; exit 130' INT TERM
    "${SSH[@]}" "$HOST" 'cd ~/hub-tests/geotech-robotics-hackathon && mkdir -p ~/hub-tests/cc-check &&
      timeout 90 python3 -m control_center --data ~/hub-tests/cc-check serve --demo' >"$tmp/server.log" 2>&1 &
    server=$!
    for _ in $(seq 1 20); do "${SSH[@]}" "$HOST" 'ss -ltn | grep -q "127.0.0.1:8780 "' && break; sleep 1; done
    "${SSH[@]}" -o ExitOnForwardFailure=yes -N -L "$LOCAL_PORT:127.0.0.1:8780" "$HOST" &
    tunnel=$!
    sleep 2
    base="http://127.0.0.1:$LOCAL_PORT"
    curl -s -o "$tmp/index.html" -w "  GET /             %{http_code} %{size_download} bytes\n" "$base/"
    curl -s -o "$tmp/sessions.json" -w "  GET /api/sessions %{http_code} %{size_download} bytes\n" "$base/api/sessions"
    sid="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sessions"][0]["id"])' "$tmp/sessions.json" 2>/dev/null)"
    [[ -n "$sid" ]] && curl -s -o "$tmp/events.json" -w "  GET /api/sessions/$sid/events %{http_code} %{size_download} bytes\n" "$base/api/sessions/$sid/events?from=0"
    cc_stop
    trap - INT TERM
    echo "  tunnel and server stopped"
    rm -rf "$tmp"
  fi
fi

exit "$remote_rc"
