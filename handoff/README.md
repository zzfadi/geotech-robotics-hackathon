# Can handoff on the real arm

"Hold my beer": the right Maker Arm takes a can from a person's hand. If that isn't working **8 of 10 times by 11:00**, the fallback picks the can from a taped mark on the table (the draft run-of-day rule in [the Saturday log](../docs/slack-log-2026-11-07.md)). Both run through the same guarded routine.

**Status (Sun Nov 8, early morning):** built and tested in replay only. It has **not moved the real arm**. Every pose in `poses.json` is an untaught placeholder from Saturday's cylinder run, and the live runner refuses to move the arm until they're taught at the station.

## How it fits

```
camera ─► vision.run (YOLO on the Orin, moments per frame) ─┐
                                                              ▼
operator keys (g / s) ─────────────────────────► handoff.run ─► pickup_server.py ─► arm
                                                 (routine + the one turn gate)   (guards, hold)
```

- **Moves** go only through `arm/tools/pickup_server.py`, the guarded holding controller from Saturday's pickup (stale feedback, temperature, speed, torque-residual and envelope guards; it freezes, never drops). The routine never talks to the motors.
- **Detector:** the routine reads `vision.run --verbose` frame records (the [hybrid model](../docs/hybrid-model.md) work) and turns each frame's moment into three facts: a hand in the arm's path, the can between the jaws, a hand still on the can. Raw YOLO boxes or a plain `{"hand_in_path": ...}` feed also work (see `detections.py`). The vision coordinator's own gate and actions are ignored, so there is exactly one turn gate, at the arm boundary.
- **Turn gate:** the existing `turn_gate.TurnGate`, unchanged. Every move is cut into short segments (3° of joint travel, about 0.3 s each) and the gate is checked before each one. A hand in the path or a stale or failed detector frame pauses before the next segment; it resumes only when the operator types `g` and the gate grants again.
- **Telemetry:** every trial is a [control center](../control_center/README.md) session, plus one line in `arm/local/handoff/trials.jsonl`.

## The sequence

**Hand mode:** operator types `g` (the "hold my beer" cue) → jaws open, guarded approach to the **offer** pose → the arm parks and waits, still, until the can is centred between the jaws with a hand on it for 0.4 s → close → the jaw angle decides: on the can, on nothing (open, offer again), or narrower than a can, like fingers (open at once, offer again); two tries → hold still until the hand is off the can for 0.5 s; if the person never lets go within 10 s, open and give it back → fresh gate grant, guarded retreat to **carry** → hold 1 s and re-read the jaw angle → success means the can is held at carry. The arm stays holding for the next beat (fist bump or showing the can).

**Mark mode (fallback):** `g` → approach **above_mark** → descend to **at_mark** → if the wrist camera's detector is running and sees no can, don't close → close, check the angle → lift → carry → same hold check.

Any failure opens the jaws if they're empty and backs out to **ready** under the same gate. An operator stop or an arm-server trip moves nothing further. A hot shoulder or elbow (49 °C; the server trips at 52 °C) stops the routine and folds back to ready.

## Run it

Offline (any laptop, standard library):

```sh
python3 -m handoff.replay                    # 17 scenarios on a fake arm, simulated time
python3 -m handoff.replay handoff/scenarios/hand_ok.json --trace
python3 -m unittest discover -s handoff -t . -p 'test_*.py' -v
```

At the station, operator on the e-stop, right arm:

```sh
# 1. The guarded arm server (holds until 'rest'), same as Saturday's pickup
PYTHONPATH=arm/tools arm/.venv/bin/python arm/tools/pickup_server.py right \
  --operator-cutoff --log arm/local/handoff/server.jsonl

# 2. Teach each pose: drive there with pickup_client.py, then record it
arm/.venv/bin/python arm/tools/pickup_client.py joints ...    # or tip / nudge
python3 -m handoff.teach offer        # ready, offer, carry, above_mark, at_mark
python3 -m handoff.teach --contact    # jaws closed on the demo can: sets the angle band
python3 -m handoff.teach --show

# 3. Rehearse with the real detector and a fake arm, then live
python3 -m vision.run --camera 0 --detect yolo26n.engine --pose yolo26n-pose.engine \
    --target real --verbose --max-seconds 3600 \
  | python3 -m handoff.run --mode hand --feed - --operator-cutoff --dry-run
# same pipe without --dry-run, plus --trials 10 --note "Saleh holding, foam can"

# Fallback, with or without the detector (operator feed: type h to fake a hand in the path)
python3 -m handoff.run --mode mark --feed operator --operator-cutoff --trials 10

# 4. The 11:00 call
python3 -m handoff.trials
```

Keys (type, then Enter): `g` go or resume, `s` stop, `h` toggle a hand in the path (operator feed only). The detector must run on the same machine as `handoff.run`: frame times are that machine's monotonic clock.

## What must be tried on the real arm Sunday morning

Station time is 9:00 to 11:30. FADI owns the handoff; Saleh is with FADI on the pickup per the draft run-of-day. The **e-stop person is still unnamed**; name one at standup, nothing below runs without them.

| # | What | Who | Pass when |
| --- | --- | --- | --- |
| 1 | Pull `main`, start `pickup_server.py` on the right arm, run `bash arm/tools/arm test` and `python3 -m handoff.replay` | FADI | Server prints `READY`; replay 17 of 17 |
| 2 | Tape the mark where Saturday's yellow cylinder stood, so Saturday's waypoints are a starting point. Teach `ready`, `above_mark`, `at_mark` (jaws at mid-can), `carry` | FADI drives, Saleh checks the side view | `handoff.teach --show` says ok for each |
| 3 | Teach `offer`: jaws open and level, at the height a person naturally holds a can, a forearm in front of the arm. Watch motor 2 and 3 temperatures while parked there for 20 s | FADI | Temperatures stay under about 47 °C after 20 s |
| 4 | Measure the jaws on the demo can (`grip -2.5` on a held can, then `teach --contact`). Then close once on nothing and once on a marker pen | Saleh | Can lands inside the band; nothing reads at or above −8°; the pen reads narrower than the band |
| 5 | Point the detector camera so the open jaws at the offer pose sit inside the yellow handoff box (`vision.run --show`; the box is `MomentConfig.handoff` in `vision/moments.py`). Hold a can in the jaws, then let go | FADI | Moments read `offer_steady` with the can in the jaws, `can_released` after letting go; frame latency p95 under 100 ms |
| 6 | Dry run: real detector, `--dry-run` fake arm, act out a handoff | FADI and Saleh | The trace reaches `done` |
| 7 | Live guard checks with the arm moving: hand in the path mid-approach (stops before the next segment, `g` resumes); keep holding (jaws open after 10 s); pull the can back (caught, jaws open) | Saleh plays the person, operator on the e-stop | Each behaves as described, nothing tugs or swings near a hand |
| 8 | Check the approach is smooth enough. If 3° segments stutter, raise `timing.segment_deg` to 5 in `poses.json` (slower reaction to a hand) | FADI | Approach under about 5 s, no visible jerk |
| 9 | Ten live hand trials (`--trials 10`); Saleh or FADI holds the can, Brian once he's on site | FADI runs, Saleh holds | `handoff.trials` says 8 of 10 or better |
| 10 | Ten live mark trials as the backup, in any gap | Saleh | 8 of 10 or better |
| 11 | **11:00 call:** `python3 -m handoff.trials`. Under 8 of 10 on the hand, demo the mark | FADI | Decision posted on the dashboard |
| 12 | Commit the taught `poses.json` before the 11:30 freeze | FADI | On `main` |

Brian is on site around 11:00, so the morning trials use a stand-in hand. If the hand passes, one run with Brian before filming.

## Limits

- **Software arbitration, not a safety system.** The station operator stays on the e-stop.
- `pickup_server.py` has no hold command, so a hand that appears during a segment stops the *next* segment, not the current one: up to about 0.4 s of motion. A real `hold` path in the server is on the Jetson brief.
- The jaw angle cannot tell a can from something the same width; it only rules out empty and too-narrow closures. The SDK caps closing and regulates 0.5 Nm on contact.
- Right arm only. The left arm missed high on Saturday and has a different claw.
- No fist bump here, and no Dream on the real arm: the hybrid router already chooses local on the real arm.
- Replay results are software results on a kinematic fake arm, not hardware evidence.
