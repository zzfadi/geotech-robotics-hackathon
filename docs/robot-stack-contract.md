# Robot stack integration boundary

Checked November 7, 2026. The selected demo remains cooperative token placement with Dream-1, LiveKit Portal, a hand-in-workspace gate and voice. This review establishes a source-level integration boundary; integrated policy-driven physical motion has not been tested here. The [two-Maker-Arm bench handoff](../arm/README.md) records supervised gripper opening over gs_usb. The later [physical control/vision exploration](../arm/docs/control-and-vision.md) inspected both arms and four cameras and completed a bounded right-wrist roll/return. Neither establishes a Dream/Portal adapter or validated whole-arm control. Joint-reference discrepancies, feedback consistency, camera registration and current USB connectivity remain to resolve before broader physical control.

## Hardware compatibility comes first

The [Portal MakerMods example](https://github.com/livekit-examples/portal-makermods/blob/5f916121c02aa782ac376be1aa1380ecc32e274c/README.md), pinned at `5f916121c02aa782ac376be1aa1380ecc32e274c`, targets **Metal** leaders and followers. It is not a demonstrated driver for the **Maker Arm** whose official model is now in this repository. See [the model assessment](maker-arm-model.md).

The [Maker Arm SDK](https://github.com/makermods-robotics/maker-arm-sdk/blob/0245316c05cb3e8d900f35e70c08e2e18e36731e/README.md), pinned at `0245316c05cb3e8d900f35e70c08e2e18e36731e`, uses six RobStride joints plus a gripper, with SocketCAN on Linux or serial SLCAN on macOS. Its documentation says joints 1–6 have no motor watchdog. A Python gate or a Portal operator switch therefore does not establish a physical stop on host failure.

Before implementing a hardware adapter, confirm the station's arm revision, installed driver, joint/gripper units and limits, camera feeds, and the station operator's hold/recovery procedure. Model geometry alone cannot establish these.

## Where the turn gate belongs

The local [turn gate](../turn_gate/README.md) accepts timestamped occupancy observations and emits turn decisions. It has no hardware or network connection.

At integration time:

1. Supply the actual sensor capture time in the gate's monotonic clock domain. A fresh network receipt or republished frame timestamp must not make an old camera frame appear fresh. Missing or invalid occupancy is a hold condition.
2. Check the gate at the robot's command execution boundary, including during an action chunk. Stopping new inference requests alone cannot revoke an existing target or queued trajectory.
3. Bind each asynchronous policy result to the turn grant that requested it. Discard results from interrupted/completed turns; obtain a fresh observation and request for the next grant.
4. Implement hold through the verified hardware driver, discard queued motion, and observe the resulting robot state. Do not translate a software `HOLD` into an assumed torque-off command.
5. Drive voice from state transitions. Repeated camera samples should not repeatedly announce the same state.

[Portal's control handoff](https://docs.livekit.io/robotics/integrations/portal/concepts/) selects the accepted operator; the underlying identity persists across disconnects. The example adds cleanup: [`robot.py`](https://github.com/livekit-examples/portal-makermods/blob/5f916121c02aa782ac376be1aa1380ecc32e274c/src/robot.py) clears its cached target on operator change/departure. Between changes, it resubmits `latest_action` each tick. Clearing that cache does not establish a measured-pose hold at the motor controller.

The example also has a freshness trap: [`Camera.read()`](https://github.com/livekit-examples/portal-makermods/blob/5f916121c02aa782ac376be1aa1380ecc32e274c/src/utils/metal.py) can return its last successful image after capture fails, and `robot.py` republishes it with a new timestamp. [`portal.yaml`](https://github.com/livekit-examples/portal-makermods/blob/5f916121c02aa782ac376be1aa1380ecc32e274c/portal.yaml) enables `reuse_stale_frames`. The future detector adapter must preserve capture freshness independently.

## Pinned example contract, not yet a Maker Arm adapter

| Interface | Inspected example |
| --- | --- |
| Portal cameras | `overhead_camera`, `left_wrist_camera`, `right_wrist_camera` |
| Portal state/action | 14 floats: left then right, six joints plus gripper each; measured positions in state and target positions in actions |
| Dream cameras | `video.overhead`, `video.wrist_l`, `video.wrist_r` |
| Dream state | `state.left_arm`, `state.left_grip`, `state.right_arm`, `state.right_grip` |
| Dream output used | `q_l_chunk`, `q_r_chunk`, matching finite arrays with seven values per row |
| Robot model requested | `ik={"arm": "metal", "head": "eef"}` |

Sources: [wire contract](https://github.com/livekit-examples/portal-makermods/blob/5f916121c02aa782ac376be1aa1380ecc32e274c/tutorial/04-wire-contract.md) and [Dream policy](https://github.com/livekit-examples/portal-makermods/blob/5f916121c02aa782ac376be1aa1380ecc32e274c/src/policies/dream_1/run.py). Joint positions are radians; gripper scale needs confirmation because executable Metal code and the YAML comment disagree. The policy discards outstanding inference results and resets its episode when control returns. Reuse that principle when binding results to gate grants, after verifying the installed SDK/API versions.

## Smallest next integration check

- Confirm station assignment and Dream-1 access with the team.
- Run the local gate replay and tests, then feed real timestamped hand observations without connecting actuators.
- Verify the exact observation/action contract against the installed station software, including camera identities, coordinate units, capture timing and chunk cancellation.
- With the station operator, validate one bounded token move and measured hold on reentry, observation loss and policy disconnect. Record that separately from the local replay result.

Camera detection, real-arm hold, Dream-1 task success and audible voice remain separate milestones. The replay does not complete them.
