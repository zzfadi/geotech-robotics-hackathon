# Jetson hub testing (Orin Nano, no arms)

**Status, Sun Nov 8, 03:47 to 04:35 PST:** the Orin Nano (`ssh orin-arm`, 192.168.8.136) runs every offline suite, serves the control center through an SSH tunnel, and runs YOLO26n + pose on its GPU from plain PyPI wheels. Nothing touched the arms, CAN/USB, `pickup_server`, Dream or any credential. All of it lives in `~/hub-tests` on the Jetson; the other checkout, `~/jetson-runtime` and `~/jetson-prep-assets` were left alone. Full numbers: [validation/jetson-hub-check-2026-11-08.json](validation/jetson-hub-check-2026-11-08.json).

## What ran

| Step | Result on the Orin (commits 132ee2e and 9ceef72) |
| --- | --- |
| `bash arm/tools/arm test` (arm venv Python, read-only) | 144 passed, 2.4 s |
| handoff unit tests | 50 OK with `ARMCTL_PYTHON` set; **2 fail in a fresh clone without it** (see below) |
| `python3 -m handoff.replay` | 20 of 20 scenarios pass |
| control center tests | 17 OK |
| vision tests, vision replay | 49 OK; replay gives one `start_handoff` (executor `local`) and one `retreat_with_can` |
| DreamSim tests (venv with numpy, scipy, mujoco) | 141 run, **2 errors** from a filename-case bug (see below), 3 skipped (need quickjs) |
| Capture tests (needs mediapipe) | 17 OK |
| Unreal Tools `test_maker_arm_model.py`, `test_maker_arm_actions.py` | 5 OK, 9 OK |
| `python3 -m handoff.preflight` | `NO-GO: fix mark poses first.` Only the untaught poses block it; detector line is `ok` with `--vision-python ~/hub-tests/venv-vision/bin/python` |
| Control center `serve --demo`, tunnel, curl from the Mac | page, `app.js`, `style.css`, `/api/sessions` and `/api/sessions/<id>/events` all 200 |
| Real sessions (`--data ~/hub-tests/sessions`) | the Mac's 4 vision/Dream-mock sessions plus 4 Jetson live sessions validate (0 invalid); all 8 come back from `/api/sessions` and their events endpoints (200) |

An independent rerun at 04:37 to 04:45 (same commit, `--detector`, `--control-center` and `--sim`) reproduced the counts above, the DreamSim errors, the preflight lines, a fresh tunnel and the still latencies below.

Detector, same 5 stills as the [Mac check](validation/vision-ultralytics-mac-2026-11-08.json), median ms per frame for detect + pose together:

| Path | Orin per frame | Per model (Ultralytics inference) | Mac M4 for reference |
| --- | --- | --- | --- |
| CPU, `.pt` | 357 to 386 | 160 to 202 | CPU 48 to 53 |
| CUDA, `.pt` (`--device 0`) | 67 to 78 | 27 to 32 | MPS 22 to 26 |
| TensorRT FP32 engines | 33 to 49 | 8 to 10 | |
| **TensorRT FP16 engines** | **26 to 41** (two runs) | **5.3 to 5.7** | |

Live C270, 30 s each, nobody in view in a dark room (the camera ran at 15 fps, not 30): CPU 1.1 fps (p50 882 ms); CUDA `.pt` 14.3 fps (p50 70 ms); FP32 and FP16 engines 15.1 fps (p50 68 ms, the camera's frame period). Junction temperature peaked at 52.5 °C during the engine builds and about 50 °C during live runs (idle 47 °C, fan 58 to 66 PWM); power stayed at the 25 W mode.

## Findings worth acting on

1. **CUDA PyTorch is plain PyPI on JetPack 7.** `pip install ultralytics` pulls `torch 2.15.0+cu132`, and `torch.cuda.is_available()` is `True` on the Orin (its sm_80 code runs on sm_87). `vision/README.md` step 2 now says so (fixed in `0306c8a`).
2. **FP16 engines need TensorRT 10.x with this Ultralytics.** With the newest PyPI `tensorrt-cu13` (11.4), `yolo export ... format=engine quantize=16` fails with `No module named 'modelopt'`; TensorRT 11 only takes FP16 through NVIDIA ModelOpt. Ultralytics 8.4.207 asks for `tensorrt-cu13>=10.15,<11`; with 10.16.1.11 the FP16 export works, about 8.5 min for detect and 11 min for pose. TensorRT 11 can build FP32 engines (about 2.5 min each). There's no system TensorRT or `trtexec` on the board, and installing one with apt would need sudo.
3. **Engines and `.pt` disagree on some frames.** The `.pt` labels match the Mac exactly on all 5 stills, but the engines (fixed 640×640) differ on 3: the side-view cylinder loses its 0.33 `bottle` and gains a second hand, so the tracker says `multiple_hands` instead of `empty_hand`. Tune and rehearse on the engines you'll actually run.
4. **DreamSim reads `provenance.json`, but git tracks `PROVENANCE.json`.** That's `DreamSim/model.py:22` and `Unreal/TheHumanTouch/Tools/maker_arm_physics.py:73`. It works on case-insensitive macOS and fails on Linux, so `vision.dream_latency` real mode would fail on the Jetson too. Fixed in `0306c8a`: both now read the tracked name, and DreamSim gives 141 OK (3 skipped) on the Jetson.
5. **Two handoff preflight tests depend on the host.** `test_go_mark_only_and_no_go_exit_codes` and `test_only_harmless_commands_run` expect `GO` but call the real `check_arm_venv()`, so they failed in any clone without `arm/.venv` or `ARMCTL_PYTHON`. Fixed in `0306c8a`: the tests point `ARMCTL_PYTHON` at the test interpreter.
6. **The small edge model runs on the Jetson.** The trained policy takes 0.038 ms per decision on the Orin's CPU (p99 0.05 ms), against a 200 ms control step. The MuJoCo physics bench passed 9 of 9 tables for both the expert and the model in 44 s. Details: [validation/edge-model-jetson-2026-11-08.json](validation/edge-model-jetson-2026-11-08.json).
7. **The CPU path is slower inside `vision.run` than in `still_check`** (877 ms against 375 ms on the same stills clip, so the camera isn't the cause). It's unexplained. Don't use the CPU path for the demo.

## Rerun it

From the Mac, in the repo root (each step only touches `~/hub-tests` on the Jetson):

```sh
scripts/jetson_hub_check.sh --setup     # first time: venv-vision, venv-trt10, venv-sim from PyPI (a few minutes)
scripts/jetson_hub_check.sh             # offline suites + preflight, about 20 s
scripts/jetson_hub_check.sh --engines   # TensorRT FP16 engines, about 20 min, once per board
scripts/jetson_hub_check.sh --all       # + DreamSim/Capture/Unreal Tools, stills, 30 s live C270, control center via tunnel
```

`JETSON_HOST` picks the host (default `orin-arm`), `CAMERA_SECONDS` sets the live run's length and `LOCAL_PORT` sets the Mac end of the tunnel. The script exits 1 if any suite exits non-zero, so `--sim` fails on Linux until the DreamSim filename case is fixed. The live step skips itself when `fuser /dev/video0` says the camera is busy, and the control center step skips itself when port 8780 is already in use on the Jetson. On Ctrl-C it still stops its tunnel and server. It copies the weights and stills from the Mac's git-ignored `vision/local/ultralytics/` when they're there. Logs go to `~/hub-tests/logs/<stamp>/`, results to `~/hub-tests/results/` and telemetry to `~/hub-tests/sessions/`.

By hand on the Jetson, after `--setup`:

```sh
cd ~/hub-tests/geotech-robotics-hackathon
export YOLO_OFFLINE=1 YOLO_CONFIG_DIR=~/hub-tests/yolo-config   # sync=false is set there
~/hub-tests/venv-trt10/bin/python -m vision.run --camera 0 \
  --detect ~/hub-tests/engines-fp16/yolo26n.engine --pose ~/hub-tests/engines-fp16/yolo26n-pose.engine \
  --device 0 --target real --max-seconds 60 --telemetry ~/hub-tests/sessions
```

The `.pt` path is the same with `~/hub-tests/venv-vision/bin/python` and `~/hub-tests/weights/yolo26n*.pt`. Engines built with TensorRT 10 load only in `venv-trt10`; the FP32 engines in `~/hub-tests/engines` load only in `venv-vision` (TensorRT 11).

## Open the control center from the Mac

```sh
ssh -t orin-arm 'cd ~/hub-tests/geotech-robotics-hackathon && python3 -m control_center --data ~/hub-tests/sessions serve'
ssh -N -L 8780:127.0.0.1:8780 orin-arm      # second terminal
```

Open http://127.0.0.1:8780 and press Ctrl-C in both terminals when you're done. Keep the `-t`: without it, closing the first terminal leaves the server running on the Jetson (checked). If that happens, run `ssh orin-arm 'pkill -u fadi -f "^python3 -m control_center --data /home/fadi/hub-tests/sessions serve"'`. Add `--demo` for simulated data. If 8780 is already taken on the Mac, use `-L 18780:127.0.0.1:8780` and open port 18780. The server stays bound to 127.0.0.1 on the Jetson.

## What the numbers mean

- **Per-frame ms** (still check) is one adapter call: detect, then pose, then conversion, excluding capture. The rest of each frame, beyond the engine's own 5 to 6 ms per model, is Python pre- and post-processing on the Orin's CPU (1.34 GHz observed).
- **Live `frame_latency_ms`** runs from the start of the camera read to the decision. When the detector beats the camera (the engines), it equals the frame period, so these runs measured the camera. When the detector is slower (CUDA `.pt`), frames queue in the V4L2 buffer and age. `max` is the first frame, which includes model load and warm-up (5 to 12 s), so warm up before going live.
- `p95 < 100 ms` from the vision README is met on the engines with headroom. A brighter room should give 30 fps from the C270.

## Still unverified

- Hands, a held can and the handoff moments on the C270: nobody was in front of it at 4 AM, and the room was dark.
- Sustained thermals over a whole demo, in a warmer room or in an enclosure; runs here lasted 30 s to 10 min.
- The `--show` preview on the KVM display, the control center page checked by eye, and the frames endpoint with real frames.
- Anything with the arms, CAN, `pickup_server` or a real Dream round trip from the Jetson; DLA and INT8 engines.
