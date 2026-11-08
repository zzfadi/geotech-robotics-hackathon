# Hybrid model: a local detector says when, Dream or a local controller says how

Written Saturday night, Nov 7, 10:30 PM, from FADI's voice call and the repo as it stood. FADI owns the can handoff on the real arm. Code: [`vision/`](../vision/README.md). Marked **[inferred]** where it is our design guess rather than something measured or decided.

## FADI's words (voice call, Nov 7, about 10 PM)

> "We will use the loop on the Jetson for moment-to-moment detection. Then we use the local YOLO … One for aim assist. One for that last centimeter interaction … plus when the network is good, to get the full movement, we send to Dream, and if the network is [slower than] 850 milliseconds, we have things handled locally. And I own the handoff."

> "The local model here is for helping when the network is bad, for the human interaction part, and for assisting, like the aim assist … the most important is the human interaction, but all of them should be able to leverage the same implementation, the same way, but then have maybe small differences."

## The design in one picture

```
camera (30 fps) ─► local detector on the Orin Nano (YOLO26n + pose, TensorRT)
                     │  boxes: hand, can (bottle/cup)
                     ▼
                  moment tracker ── moments: hand_entered, offer_started,
                     │                       offer_steady, can_released,
                     │                       offer_withdrawn, observation_invalid
                     │  motion_ok per frame
                     ▼
                  turn gate (existing, unchanged) ── HOLD / ROBOT, grant ids
                     │  robot_turn_granted
                     ▼
                  router ── Dream for the full move if median RTT ≤ 850 ms
                     │      local (scripted grasp, later edge model) otherwise
                     ▼
                  arm adapter ── start_handoff / abort / retreat_with_can
                                 last centimetre always local (assist)
```

One implementation, three uses, as FADI asked: the same detector, tracker and router serve the **human handoff** (top priority), **aim assist** (the last-centimetre snap toward the can, same tracker output, used while a person drives the arm), and **lag bridging** (the router's local path when Dream is slow). They differ only in who requests the turn and what the local controller does.

## What the detector watches and what it emits

The existing turn gate only lets the arm move when **no hand** is in view, which suits token placement but not "hold my beer", where the arm has to reach toward a hand. The moment tracker in `vision/moments.py` decides which hand presence is acceptable, then feeds the gate `hand_present = not motion_ok`, so the gate's freshness, debounce, grant ids and revocation work unchanged.

| Moment | When | Motion allowed |
| --- | --- | --- |
| (empty) | No hand in the keep-out zone | Yes, but nothing requests a turn |
| `hand_entered` | A hand without a can, or two hands | No |
| `offer_started` | One hand holding a can (boxes overlap) in the handoff zone | No, not yet steady |
| `offer_steady` | The can moved under 0.08 frame widths/s for 0.4 s | Yes; this is what sends `request_turn` |
| (engaged) | Still held; up to 0.3 s of the can hidden by the gripper is tolerated | Yes, unless the can jerks (over 0.6 widths/s) |
| `can_released` | The hand has been off the can for 0.3 s, or gone | Yes; the arm retreats with the can |
| `offer_withdrawn` / `hand_returned` | Can jerked away, can lost, second hand, hand back on a released can | No; the gate revokes and the adapter aborts |
| `observation_invalid` | Detector failure or out-of-order frame | No; fails closed |

All thresholds are **[inferred]** first guesses for a webcam at arm's length and need tuning on the real table. This answers the turn-gate milestone "decide what sends `request_turn`" with a proposal: a steady offered can, not a key press. The team still has to agree.

## When Dream is called and when it is local

`vision/hybrid.py`'s `ExecutorRouter` picks at the moment the turn is granted:

| Condition | Who moves the arm |
| --- | --- |
| Real arm (default) | Local scripted grasp. The team's call is that Dream-1 is not needed on the physical arm and stays in the sim; `dream_on_real=True` overrides it. |
| Dream unavailable, or 2 failed requests in a row | Local |
| Median of the last 5 round trips over 850 ms | Local (FADI's rule) |
| Otherwise | Dream, async, for the full movement, with the prompt "Take the can from the person's hand." **[inferred prompt]** |
| Every case | The last centimetre near the can is the local assist's job |

Two cautions. Dream's own client switches itself from async to sync above a 700 ms median by default, and the workshop advice was to wait for a late reply and never skip ahead in a chunk. So the router decides per turn, and inside a Dream turn the client's own sync mode applies; the local controller only takes over between turns or when Dream fails. Changing the prompt mid-episode is allowed (it's a field of each observation) but its effect isn't documented, so call `reset_episode()` when the task changes **[inferred]**.

## Latency budget [inferred until measured]

| Step | Budget | Basis |
| --- | --- | --- |
| Camera frame | 33 ms | 30 fps |
| YOLO26n detect + pose, TensorRT FP16 | about 10 ms | 4.6 ms per model on an Orin Nano Super, inference only (Ultralytics) |
| Pre/post-processing | 5 to 15 ms | not published; measure |
| Frame age at the gate | under 250 ms | existing gate limit |
| Steady offer + gate debounce | 0.4 s + 0.2 s | tracker and coordinator defaults |
| Dream round trip | about 712 ms median, 850 ms switch | Dream workshop |
| Dream chunk | 48 actions, 2.4 s, next reply after about 30 | Dream workshop and client docs |

So from "can held still" to the arm starting is about 0.6 s plus the executor's start-up. The local detector reacts to a jerk or a second hand within one or two frames (under 100 ms); whether the arm actually stops that fast depends on the arm controller's hold, which is not verified.

## What runs on the Orin Nano

- The detector loop: `python3 -m vision.run --camera 0 --detect yolo26n.engine --pose yolo26n-pose.engine --target real`.
- The arm server and its guarded controller ([Jetson brief](handoffs/jetson-end-to-end.md)); the detector's `abort` must reach the controller's verified hold.
- Later, the edge model ([brief](handoffs/edge-model.md)) as the `local_edge_model` mode.
- Dream calls go out from whichever machine has `DREAM_API_KEY`; for the sim that is the Mac worker today.

The Orin is still being prepared on FADI's Mac Mini. None of this has run on it.

## Which local model: options and recommendation

"YOLO TensorRT" just means a YOLO model converted with NVIDIA's TensorRT into an `.engine` file tuned for the Jetson's GPU. It's the same model, about 3 to 4 times faster than plain PyTorch on the Orin.

| Option | Covers | Speed on Orin | Effort by Sunday |
| --- | --- | --- | --- |
| **YOLO26n detect + YOLO26n-pose, TensorRT FP16 (recommended)** | Cans as COCO "bottle"/"cup"; hands from the pose model's wrist keypoints | about 4.6 ms per model (Orin Nano Super) | Pretrained, export only |
| MediaPipe Hand Landmarker (already in `turn_gate/`) | 21 hand landmarks | CPU only on Orin, about 5 fps reported | Already written; too slow for the jerk check |
| YOLO26n fine-tuned on our can | A real "can" class | same as above | 200 to 400 labelled frames of our can on our camera **[inferred]**; only if COCO misses it |
| YOLO26n-pose on the hand-keypoints dataset | 21 keypoints per hand | same | 26k images, non-commercial licence, training time; skip for Sunday |

COCO-80 has `person`, `bottle` and `cup` but no "can" and no "hand" class, so the tracker treats bottle or cup as the can and builds hand boxes just past each wrist. Check first thing on the real camera whether the can is detected at all; if not, a small labelled set of our own can is the fallback. YOLO26 is the current Ultralytics model; YOLO11 works the same way if the Orin's Ultralytics version is older.

Install on JetPack 6 per the Ultralytics Jetson guide (pip `ultralytics` with its Jetson torch wheels, or its JetPack 6 Docker image), then export each model once:

```sh
yolo export model=yolo26n.pt format=engine quantize=16
yolo export model=yolo26n-pose.pt format=engine quantize=16
```

Older Ultralytics releases spell `quantize=16` as `half=True`.

## How Dream takes observations

From the repo's working client (`DreamSim/probe.py`, `dreamlabs-client==1.1.3`) and the client's PyPI page:

- `DreamClient()` reads `DREAM_API_KEY`; `client.policy({...})` once, `set_observation(frames, observation)` each camera tick, `get_action()` on one worker thread, `reset_episode()` between trials.
- Frames: one base64 JPEG per camera, `video.overhead`, `video.wrist_l`, `video.wrist_r`, at 320×176. The base model needs all three roles.
- State: six joint radians per arm plus gripper opening 0 to 1 (our probe maps it from the 0.104825 m jaw gap). `prompt` is a field of the observation.
- Action: `q_l_chunk`, `q_r_chunk`, 48 rows of seven values (six joints plus gripper).

Base Dream-1 is a two-arm model; single-arm use on our Maker Arm is unconfirmed by the vendor, which is one more reason the real arm defaults to the local grasp.

## What's built and what's open

Built and tested in the cloud (no camera, no arm): the moment tracker, the router, the coordinator that drives the existing turn gate, a replay CLI, an example handoff trace, an optional Ultralytics adapter, and a live webcam loop with a preview window, a session summary and recording to replay traces. 28 tests pass. The adapter and live loop are tested against stand-ins only; they have not run with real Ultralytics models or a camera, because the PyTorch download was blocked from the cloud machine.

Tonight's test on the Orin Nano with a webcam and no arm is step by step in [vision/README.md](../vision/README.md#tonight-orin-nano-plus-a-webcam-no-arm).

Open for the 9 AM standup:

1. Does the team accept "a steady offered can sends `request_turn`"? It changes the gate's role from "no hand" to "only the offering hand".
2. Run the detector on the real camera and the real can: is the can detected, and as what?
3. Tune the zones and thresholds on the table; record a few clips as replay traces. Keep one clip with a different person or can out of tuning and score on it: false starts (`offer_steady` when the can was only carried past, set down or held while talking), missed offers, delay from still to `offer_steady`, and state flicker. A synthetic or sim trace is not a real-camera result. Record people only with their OK; `--record` traces keep labels and boxes, not images, so keep it that way.
4. Wire `start_handoff` / `abort` / `retreat_with_can` into FADI's arm controller and verify its hold.
5. Measure detector latency on the Orin once it boots.

## Sources

- Ultralytics NVIDIA Jetson guide and benchmarks: https://docs.ultralytics.com/guides/nvidia-jetson/
- Ultralytics export modes: https://docs.ultralytics.com/modes/export/
- COCO classes: https://docs.ultralytics.com/datasets/detect/coco/
- Pose task and COCO keypoints: https://docs.ultralytics.com/tasks/pose/
- Hand-keypoints dataset: https://docs.ultralytics.com/datasets/pose/hand-keypoints/
- MediaPipe on the Orin Nano, CPU fallback: https://forums.developer.nvidia.com/t/hand-tracking-on-jetson-orin-nano/297754
- Dream client: https://pypi.org/project/dreamlabs-client/ and [DreamSim/README.md](../DreamSim/README.md)
- Dream workshop advice: [dream-workshop-takeaways.md](handoffs/dream-workshop-takeaways.md)
