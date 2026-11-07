# Local turn gate

A hardware-independent prototype for the Human Touch token-placement demo. A fresh, continuously clear workspace plus an explicit turn request can grant a robot turn. Hand reentry or unusable observations revoke it. Completion returns control to the person.

The gate emits software decisions only. The optional [MediaPipe camera preview](CAMERA.md) supplies hand observations and keyboard turn requests. Neither connects Dream-1, LiveKit or an arm, or verifies a physical stop. The [integration boundary](../docs/robot-stack-contract.md) records what remains to connect it to the actual station.

## Run locally

From the repository root, with Python 3.9 or later and no third-party dependencies:

```sh
python3 -m turn_gate.replay turn_gate/examples/turn_taking.jsonl
python3 -m unittest discover -s turn_gate -p 'test_*.py' -v
```

Replay uses supplied timestamps, not wall-clock delays. Its output is JSON suitable for inspecting decisions and later feeding a UI or voice adapter. The example covers a normal turn, interrupted motion, recovery through a new request, and observation loss.

## Behavior

| Condition | Result |
| --- | --- |
| Startup or no valid observation | `HOLD` |
| Hand present, invalid/stale capture, future or non-increasing capture timestamp | `HOLD`; discard pending/active turn |
| Fresh clear captures without a request | Continue waiting |
| Explicit request plus continuous fresh clear captures for the debounce interval | Grant one `ROBOT` turn |
| Hand returns during that turn | Revoke the grant; clearing the hand does not automatically resume it |
| Matching robot completion | Yield to the person; require new observations and a new request |
| Completion from an old grant | Cannot complete a newer turn |

The prototype defaults to 0.5 seconds of clear observations and a 0.25-second freshness limit. These are configurable demo parameters, not measured detector latency or validated robot stopping times. Only new observations advance the clear interval; a clock tick by itself cannot grant a turn.

Send a turn request after the first fresh hand-clear observation. Requests made before that, or while a hand is present, are ignored. A pending request can wait through the clear debounce, but any observation failure cancels it.

## Adapter contract

Use `TurnGate` with an injected monotonic clock for replay, or its default clock for a local process. Supply `Observation(observed_at, hand_present, valid=True)`, where `observed_at` is the **actual capture time in that clock domain**. Camera receipt time, inference completion time, repeated cached frames and raw wall-clock/remote timestamps are not interchangeable with capture time. Aggregate all relevant hands into `hand_present`; an unavailable detector must report invalid data or stop supplying observations.

`observe(...)`, `request_turn()`, `complete_turn(grant_id)` and `tick()` return structured event tuples. A grant has an identifier; keep it with any asynchronous policy request and reject results whose grant is no longer active. Grant identifiers are local to one gate instance; a future network adapter must also distinguish process/session restarts.

Call `tick()` regularly and immediately before deciding whether a command may execute, even when observations stop arriving. `snapshot()` is a cached view with an evaluation timestamp; reading it does not refresh the gate. A stalled process cannot enforce this software gate, so the hardware controller needs its own verified failure behavior.

Keep all gate calls serialized. At the robot execution boundary, a revoked grant must invalidate queued actions and trigger the station's verified hold behavior. A stopped inference stream or a changed Portal operator alone does not prove that hold. Emit voice from transition events so repeated samples do not repeat speech.
