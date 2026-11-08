# Control center

One calm page that answers three questions about the arm: **what is it doing, is it healthy, and why did it make each decision.** It shows a live session as it runs and any past session from the same files, with one playhead for both.

It is read-only. It never commands the arm, never holds a key, and binds to `127.0.0.1` by default.

## Run it

Python 3.9 or newer, standard library only. From the repo root, on the Mac or on the Orin Nano:

```
python3 -m control_center serve --demo
```

Then open http://127.0.0.1:8765. `--demo` writes three finished demo sessions (if the folder is empty) and runs a live simulated one, so every view has data before the arm is wired. Demo sessions say so in a banner.

Without `--demo` it serves whatever is in the sessions folder: `control_center/local/sessions/` (git-ignored), or `--data <folder>`, or `$CONTROL_CENTER_DATA`.

**From the Mac, with the server on the Orin Nano:** keep it on localhost there and tunnel the port.

```
ssh -N -L 8765:127.0.0.1:8765 <user>@<orin-host>
```

Then open http://127.0.0.1:8765 on the Mac.

Other commands:

```
python3 -m control_center demo --minutes 15 --speed 4   # a live demo session, 4x time
python3 -m control_center history --count 3              # finished demo sessions
python3 -m control_center validate <session id or path>  # check events against the schema
python3 -m control_center schema                         # print the JSON Schema
python3 -m unittest discover -s control_center/tests -t .
```

## What the page shows

| Panel | What it answers |
| --- | --- |
| **Now** | The task and step, the attempt, whether it is holding for a late reply, and a camera fault if there is one. The left bar is green when running async, amber in sync, red on a failure or fault. |
| **Round trip** | Median of the last 5 round trips, against the 850 ms line, with the recent trend. |
| **Loop mode** | Async or sync, when it last switched, and the median that made it switch. |
| **Commanded speed** | The applied speed against the 0.85 m/s limit, and what the reply asked for when it was clamped. |
| **Cameras** | How many of the three pass all five workshop checks (colours, upright, right view, present, aspect). |
| **What the model saw** | The exact 320×176 frame sent with the latest request, with a warning over it when that camera fails a check. |
| **Decisions** | Who decided what, and why, in one sentence each: Dream, the edge model, the operator, assist, the loop, safety or the controller. Routine step changes show in the Now card instead. |
| **Session timeline** | Attempts, mode bands, every round trip against the 850 ms line, speed clamps, drops and camera faults. Click or drag to look back; arrow keys step 5 s (Shift for 30 s); **Live** returns to the newest event. |
| **Details** | The camera checklist per camera, network totals (median, 95th percentile, late and failed requests, upload size), and the last action chunk: where the reply landed, how many actions blended, and that none were skipped. |

These follow the [Dream workshop takeaways](../docs/handoffs/dream-workshop-takeaways.md).

## Writing telemetry

The contract is `geotech.telemetry/1`, defined in [telemetry.py](telemetry.py) and exported as [telemetry.schema.json](telemetry.schema.json). A session is a folder with an append-only `events.jsonl` and a `frames/` folder for the images the model was sent. Anything that runs the arm writes it from its first run: the Dream loop in the sim, the Orin Nano arm server, the [edge model](../docs/handoffs/edge-model.md), teleop.

```python
from control_center.telemetry import Writer

with Writer('control_center/local/sessions', component='arm-server') as w:
    w.emit('session', phase='start', robot='Maker Arm, right', purpose='live', station='Table 10',
           limits={'rtt_threshold_ms': 850, 'window': 5, 'speed_limit_m_s': 0.85})
    w.emit('network', phase='sent', request_id='r0001', upload_bytes=88_000, frame_size=[320, 176])
    w.emit('network', phase='received', request_id='r0001', rtt_ms=712.4)
    w.emit('command', requested_m_s=2.11, applied_m_s=0.85, limit_m_s=0.85, clamped=True)
    w.emit('decision', by='safety', what='Clamped speed from 2.11 to 0.85 m/s',
           why='The reply asked to move faster than the limit after a wait')
```

| Kind | Required fields | Written when |
| --- | --- | --- |
| `session` | phase, robot, purpose | the run starts and ends (`limits` sets the lines the page draws) |
| `task` | phase, name | an attempt starts, changes step, and ends with an outcome |
| `network` | phase, request_id | a request is sent, is late, is received (with `rtt_ms`) or fails |
| `mode` | mode, median_ms, threshold_ms | the loop switches between async and sync |
| `chunk` | request_id, actions, arrived_at_action, blended, skipped | a reply's chunk is played (`skipped` must be 0) |
| `command` | requested_m_s, applied_m_s, limit_m_s, clamped | a speed is commanded |
| `decision` | by, what, why | anyone decides something a person would want explained |
| `camera` | role, ok, checks | camera health is checked |
| `frame` | role, path, width, height | a frame is sent to a model (save it under `frames/`) |
| `contact` | what | a grasp, release, drop or collision is detected |
| `state` | q, grip | measured arm state, at a low rate |

The writer validates every event, flushes each line so the page sees it at once, and drops fields set to `None`. Several components can write to one session; the page sorts by time. `decision.what` and `decision.why` are the only free text a person reads, so the page never makes up a reason.

## What is not tested yet

- Everything here has run against the demo stream only, on Linux. Nothing has run on the Orin Nano or the Mac, or with the real arm, the Dream API or real cameras.
- The page has been exercised in a scripted test of its logic, not checked by eye in a browser yet.
- No writer exists yet in the sim loop or the arm server; adding one is part of [edge-model.md](../docs/handoffs/edge-model.md) and [jetson-end-to-end.md](../docs/handoffs/jetson-end-to-end.md).
- The camera checks are fields in the schema; nothing computes them from real images yet.
