# Can handoff on the real arm

"Hold my beer": the right Maker Arm picks the can up. **The live beat is the taped mark** (the arm picks the can from a mark on the table), as in the [stage outline](../demo/pitch/deck/outline.md). The hand-to-arm handoff replaces it only if it reaches **8 of 10** live trials before the call. Both run through the same guarded routine.

**Status (Sun Nov 8, early morning):** built and tested in replay only. It has **not moved the real arm**. Every pose in `poses.json` is an untaught placeholder from Saturday's cylinder run, and the live runner refuses to move the arm until they're taught at the station.

## How it fits

```
camera ─► vision.run (YOLO, moments per frame) ─────────────┐
                                                              ▼
operator keys (g / s) ─────────────────────────► handoff.run ─► pickup_server.py ─► arm
                                                 (routine + the one turn gate)   (guards, hold)
```

- **Moves** go only through `arm/tools/pickup_server.py`, the guarded holding controller from Saturday's pickup (stale feedback, temperature, speed, torque-residual and envelope guards; it freezes, never drops). The routine never talks to the motors.
- **Detector:** the routine reads `vision.run --verbose` frame records (the [hybrid model](../docs/hybrid-model.md) work) and turns each frame's moment into three facts: a hand in the arm's path, the can between the jaws, a hand still on the can. Raw YOLO boxes or a plain `{"hand_in_path": ...}` feed also work (see `detections.py`). The vision coordinator's own gate and actions are ignored, so there is exactly one turn gate, at the arm boundary.
- **Turn gate:** the existing `turn_gate.TurnGate`, unchanged. Every move is cut into short segments (3° of joint travel, about 0.3 s each) and the gate is checked before each one. A hand in the path or a stale or failed detector frame pauses before the next segment; it resumes only when the operator types `g` and the gate grants again.
- **Telemetry:** every trial is a [control center](../control_center/README.md) session, plus one line in `arm/local/handoff/trials.jsonl`.

## The sequence

**Hand mode (only if it reaches 8 of 10):** operator types `g` (the "hold my beer" cue) → jaws open, guarded approach to the **offer** pose → the arm parks and waits, still, until the can is centred between the jaws with a hand on it for 0.4 s → close → the jaw angle decides: on the can, on nothing (open, offer again), or narrower than a can, like fingers (open at once, offer again); two tries → hold still until the hand is seen off the can and stays off for 0.5 s, watched from the moment the jaws start closing (see the occlusion note); if the person never lets go within 10 s, open and give it back → fresh gate grant, guarded retreat to **carry** → hold 1 s and re-read the jaw angle → success means the can is held at carry. The arm stays holding for the next beat (fist bump or showing the can).

**Mark mode (the default live beat):** `g` → approach **above_mark** → descend to **at_mark** → if the wrist camera's detector is running and sees no can, don't close → close, check the angle → lift → carry → same hold check.

**Occlusion fail-safe.** "No hand on the can" is not enough to let go of a person. `vision.moments` reads "no hand in view while the can is" as `RELEASED` (`can_without_hand`), and a covered lens reads as `EMPTY`; a hand hidden behind the can looks the same. So the release only counts once the jaw camera has **seen a hand in view and off the can** since it was last on it, with the can between the jaws. After that the hand may leave the view (people pull their hand back), but a frame with no can in view (a covered lens, a feed restart) or a stale feed drops the seen release: it has to be seen again. The watch starts when the jaws start closing, so a person who lets go during the 3 s close and then lowers their hand still counts. A hand on the can, then no hand at all, is treated as uncertain: the arm keeps holding still. If the release is never seen within the same 10 s, it opens the jaws to give the can back (reason `release_not_seen`) and never carries it away. If the person had in fact let go, the can drops when the jaws open, so keep a hand under it at the station. Replays: `hand_occluded_while_holding`, `hand_lens_covered_while_holding`, `hand_seen_off_then_lens_covered`, `hand_let_go_then_left_view`, `hand_let_go_during_close`. The new `hand_visible` fact comes from the box feed (any hand box) and from vision states (`EMPTY` and `RELEASED`/`can_without_hand` are false); a flags feed that leaves it out never counts as a seen release.

Any failure opens the jaws if they're empty and backs out to **ready** under the same gate. An operator stop or an arm-server trip moves nothing further. A hot shoulder or elbow (49 °C; the server trips at 52 °C) stops the routine and folds back to ready.

## Run it

Offline (any laptop, standard library):

```sh
python3 -m handoff.preflight                 # GO / MARK-ONLY / NO-GO, never touches hardware
python3 -m handoff.replay                    # 22 scenarios on a fake arm, simulated time
python3 -m handoff.replay handoff/scenarios/hand_ok.json --trace
python3 -m unittest discover -s handoff -t . -p 'test_*.py' -v
```

**Preflight** prints one line per check and a verdict. It reads git (HEAD against the cached `origin/main`; `--fetch` refreshes it), checks that `127.0.0.1:8765` is free or held by `pickup_server.py` for the arm in `poses.json` (it names the owning process; a server for the other arm is NO-GO), that `arm/.venv` exists, which poses are taught, whether the grip band was measured, and whether `cv2` and `ultralytics` import in the interpreter that will run `vision.run` (`--vision-python PATH` if that is a venv). `--run-tests` also runs `bash arm/tools/arm test`, the handoff unit tests, `handoff.replay` and `handoff.teach --show`. Verdicts: **GO** both modes can run; **MARK-ONLY** no detector, a hand pose untaught or the grip band unmeasured, so only the mark; **NO-GO** fix the NO-GO lines first. Exit code 0, 3 or 1.

At the station, right arm. **The e-stop person is named at the 8:30 hardware check, before row 1 starts the server.** The server enables the motors as soon as it starts, so start it only with the e-stop person at the stop. Row numbers match the table below.

```sh
# Row 0, fresh laptop only (needs the internet; details in arm/README.md). Python 3.12 is the
# minimum for the pinned NumPy and SciPy; requirements.txt fetches the Maker Arm SDK at a pinned commit.
brew install libusb uv
uv venv --python 3.12 arm/.venv
uv pip install --python arm/.venv/bin/python -r arm/requirements.txt
bash arm/tools/arm test
# With an existing environment instead, set ARMCTL_PYTHON to its python and use it for arm/.venv/bin/python below.

# Row 1. Before teaching, expect "NO-GO: fix mark poses first" with "mark poses" the only NO-GO
# line (MARK-ONLY lines for hand poses, the grip band and maybe the detector are expected): that is
# the cue to start the server and teach. Fix any other NO-GO first.
python3 -m handoff.preflight --run-tests
# E-stop person at the stop, then the guarded arm server (holds until 'rest'); wait for READY.
# The libusb path is what bash arm/tools/arm sets for its own tools (arm/README.md).
DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix libusb)/lib" PYTHONPATH=arm/tools \
  arm/.venv/bin/python arm/tools/pickup_server.py right \
  --operator-cutoff --log arm/local/handoff/server.jsonl

# Rows 2-3. Teach each pose: drive there with pickup_client.py, then record it
arm/.venv/bin/python arm/tools/pickup_client.py joints ...    # or tip / nudge
python3 -m handoff.teach at_mark      # ready, above_mark, at_mark, carry (offer is row 7)
python3 -m handoff.teach --contact    # jaws closed on the demo can: sets the band (hand mode refuses without it)
python3 -m handoff.teach --show

# Row 4. The mark, with or without the detector (operator feed: type h to fake a hand in the path)
python3 -m handoff.run --mode mark --feed operator --operator-cutoff --trials 10

# Hand mode only, once, before row 6: the detector's own venv. ultralytics pulls torch and OpenCV
# (with the preview window); the first run downloads yolo26n.pt and yolo26n-pose.pt (internet).
uv venv --python 3.12 vision/local/.venv
uv pip install --python vision/local/.venv/bin/python ultralytics
python3 -m handoff.preflight --vision-python vision/local/.venv/bin/python
# Find the jaw camera: index 0 on a MacBook is usually the built-in camera. Try 1, 2, ... until the
# preview shows the open jaws. Indices change after recabling, so recheck after any USB move.
vision/local/.venv/bin/python -m vision.run --camera 1 --device mps --show --max-seconds 10

# Rows 9-12, only after preflight says GO. vision.run stops after --max-seconds (default 60).
vision/local/.venv/bin/python -m vision.run --camera N --detect yolo26n.pt --pose yolo26n-pose.pt \
    --device mps --target real --verbose --max-seconds 3600 \
  | python3 -m handoff.run --mode hand --feed - --operator-cutoff --dry-run
# same pipe without --dry-run, plus --trials 10 --note "Saleh holding, foam can"
# On the Orin only, after a TensorRT export: --detect yolo26n.engine --pose yolo26n-pose.engine --device 0

# Row 13. The call
python3 -m handoff.trials
```

**Stopping the arm.** Take the can out of the jaws and keep the path clear, then `arm/.venv/bin/python arm/tools/pickup_client.py rest`: it opens the jaws, returns the arm to the pose where the server enabled it, releases the motors and ends the server. If the reply says `"release_acknowledged": false`, the server is still holding: call the e-stop person. `handoff.run` leaves the arm enabled and holding when it ends. **Never Ctrl-C the server**: it prints `SERVER_EXIT_WITH_ARM_ENABLED` and keeps holding, and a second Ctrl-C ends it with no return or release, motors still enabled. Use the physical cutoff.

Keys (type, then Enter): `g` go or resume, `s` stop, `h` toggle a hand in the path (operator feed only). The detector must run on the same machine as `handoff.run`: frame times are that machine's monotonic clock. Run `vision.run` with the interpreter preflight checked. With missing `.pt` files, ultralytics tries to download them on first use, which needs the internet.

The [control center](../control_center/README.md) now serves on **127.0.0.1:8780**, so it no longer collides with the arm server on 8765. `python3 -m control_center serve`, then open http://127.0.0.1:8780.

## What must be tried on the real arm Sunday morning

Mark first. The station opens about **8:30**, the call is at **11:00** and the freeze at **11:30**. FADI owns the arm; Saleh is with FADI on the pickup per the draft run-of-day. The e-stop person is named at the 8:30 hardware check, before row 1 starts the server; nothing below runs without them.

| # | What | Who | Pass when |
| --- | --- | --- | --- |
| 0 | Fresh laptop only: the arm venv (row 0 above) | FADI | `bash arm/tools/arm test` passes |
| 1 | Pull `main`. Run `python3 -m handoff.preflight --run-tests`, then, with the e-stop person at the stop, start `pickup_server.py` on the right arm | FADI | Before teaching, preflight says `NO-GO: fix mark poses first` and `mark poses` is its only NO-GO line; server prints `READY` |
| 2 | Tape the mark where Saturday's yellow cylinder stood, so Saturday's waypoints are a starting point. Teach `ready`, `above_mark`, `at_mark` (jaws at mid-can), `carry` | FADI drives, Saleh checks the side view | `handoff.teach --show` says ok for each |
| 3 | Grip and contact calibration on the demo can: `grip -2.5` on a held can, then `teach --contact` (hand mode refuses to run without it). Then close once on nothing and once on a marker pen | Saleh | Can lands inside the band; nothing reads at or above −8°; the pen reads narrower than the band |
| 4 | Ten live mark trials (`--mode mark --trials 10`). Aim to lock the mark by about **10:15** | Saleh runs, FADI on the side view | `handoff.trials` says the mark is 8 of 10 or better |
| 5 | Optional: teach a `fist` pose for the fist-bump beat (`python3 -m handoff.teach fist`). The routine does not use it; play it with `pickup_client.py joints` | FADI | `teach --show` lists `fist ok` |
| 6 | After the mark trials, re-run `python3 -m handoff.preflight --vision-python vision/local/.venv/bin/python` (hand-mode setup above, once). Go to row 7 only if its one MARK-ONLY line is `hand poses`, `not taught yet: offer` (expected); fix anything else first | FADI | Only `offer` left |
| 7 | Teach `offer`: jaws open and level, at the height a person naturally holds a can, a forearm in front of the arm. Watch motor 2 and 3 temperatures while parked there for 20 s. Re-run the row 6 preflight (with `--vision-python`): **rows 8 to 12 only if it says GO** | FADI | Temperatures stay under about 47 °C after 20 s; preflight says `GO` |
| 8 | Point the detector camera so the open jaws at the offer pose sit inside the yellow handoff box (`vision.run --show`; the box is `MomentConfig.handoff` in `vision/moments.py`). Hold a can in the jaws, then let go | FADI | Moments read `offer_steady` with the can in the jaws, `can_released` after letting go; frame latency p95 under 100 ms |
| 9 | Dry run: real detector, `--dry-run` fake arm, act out a handoff. Also cover the lens with a hand while the can is held | FADI and Saleh | The trace reaches `done` (a `release_not_seen` after a clean let-go means the camera lost the can in the jaws: re-aim it); with the lens covered it keeps holding (see the occlusion note) |
| 10 | Live guard checks with the arm moving: hand in the path mid-approach (stops before the next segment, `g` resumes); keep holding (jaws open after 10 s); pull the can back (caught, jaws open) | Saleh plays the person, operator on the e-stop | Each behaves as described, nothing tugs or swings near a hand |
| 11 | Check the approach is smooth enough. If 3° segments stutter, raise `timing.segment_deg` to 5 in `poses.json` (slower reaction to a hand) | FADI | Approach under about 5 s, no visible jerk |
| 12 | Ten live hand trials (`--mode hand --trials 10`); Saleh or FADI holds the can, Brian once he's on site | FADI runs, Saleh holds | `handoff.trials` says 8 of 10 or better |
| 13 | **11:00, the call:** `python3 -m handoff.trials`. **The mark is the live beat unless hand mode reached 8 of 10.** | FADI | Decision posted on the dashboard |
| 14 | Commit the taught `poses.json` before the 11:30 freeze | FADI | On `main` |

Brian is on site around 11:00, so the morning trials use a stand-in hand. If the hand passes, one run with Brian before filming.

## Limits

- **Software arbitration, not a safety system.** The station operator stays on the e-stop.
- `pickup_server.py` has no `hold` command. It handles `status`, `move`, `grip`, `clear`, `rest` and `release_supported`, and a `move` blocks until it finishes. So a hand that appears during a segment stops the *next* segment, not the current one: up to about 0.4 s of motion. A real `hold` path in the server is on the Jetson brief.
- `pickup_server.py` binds 127.0.0.1:8765 before it connects to or enables the arm, so a busy port makes it print `PORT_BUSY` and exit 1 before it even looks for the USB adapter: the motors are never connected or enabled. A client that connects while the arm is still starting is dropped unserved (the server prints `REFUSED n connection(s) made before READY`), so start `handoff.run`, `teach` or `pickup_client.py` only after `READY`.
- The jaw angle cannot tell a can from something the same width; it only rules out empty and too-narrow closures. The SDK caps closing and regulates 0.5 Nm on contact.
- Right arm only. The left arm missed high on Saturday and has a different claw.
- No fist bump in the routine (the optional `fist` pose is played by hand with `pickup_client.py joints`), and no Dream on the real arm: the hybrid router already chooses local on the real arm.
- Replay results are software results on a kinematic fake arm, not hardware evidence.
