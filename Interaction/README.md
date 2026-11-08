# Table 10: virtual contacts → Maker Arm bridge

Table 10 is the separate `MakerInteraction_T09__` interaction bay. It is **not**
fleet index9; the cup fleet remains indices0–8. This package implements an
offline protocol, deterministic force conversion and a read-only SDK sampling
adapter. It has **not** connected to hardware or sent any motor command. The
SDK host has not been chosen; the user deferred that decision until tomorrow.

## Contract

`protocol.validate_contact(event, now)` accepts version1/Table10 packets with:

- `session_id`: registered32-character lowercase hex nonce; new host session
  requires a new nonce. `side`: `left` or `right`, matching the single-arm lease.
- `source_id`, `source_kind` (`hand`/`object`), nonnegative increasing `seq`.
- `captured_unix`: age≤100ms with no future timestamps. Broker independently
  stamps receipt and expires the latest packet after100ms. No replayed force.
- `link`: official movable arm link `link_002` through `link_007`; base and gripper links excluded.
- `frame: urdf_base_rh`, `point_m[3]`, `force_n[3]`. Force norm≤5N and point norm≤1.5m; finite values.

`ContactLease(session_id, side)` rejects old sessions, cross-arm packets and
reordered sequences. `current(now)` returns `None` when stale, never the last
force indefinitely. A session nonce is identity/freshness, **not authorization**;
the broker requires distinct controller and UE producer credentials.

The relay exposes authenticated controller session/state/contact polling and a
separate authenticated contact producer. The web panel only observes and stops;
it cannot enable physical motion or fabricate local operator presence.

## Coordinates and control proposals

`ue_contact_to_urdf(point_cm, force_n, world_from_urdf)` uses a registered transform
mapping RH URDF metres into LH Unreal metres. Positions convert centimetres to
metres; force stays newtons. Torque is calculated after conversion, avoiding
incorrect axial-vector reflection. A correct virtual scene transform does not
calibrate the physical arm.

`point_jacobian` follows the pinned official URDF ancestors for the contacted
link. `contact_torque` computes `J_point.T @ force`. A separately registered,
validated hardware mapping `q_urdf = A @ q_sdk + b` maps torque by virtual work:
`tau_sdk = A.T @ tau_urdf`. There is no default identity hardware calibration.

`bounded_admittance` returns a **position proposal**, not direct torque mode:

- Existing six-joint bench effort guards `[1.5,5,6,2.5,1,1] Nm`; virtual effort
  uses remaining measured-effort headroom. This is not a proven motor-force cap.
-25Hz maximum timestep,3°/s demand rate,2° maximum lead, strict nominal SDK limits.
- Fresh feedback≤250ms, measured speed≤20°/s, temperature<42°C, no faults.
- Contact loss returns `retain_guarded_hold`, zero virtual torque, no target
  return or unconditional release. No inertia/gravity compensation is invented.

`calibrated_plan` additionally requires matching side/serial/calibration ID,
validation provenance and local deadman. Even when these pass, it returns
`physical_output_enabled: false`: this revision has no physical output entrypoint.

## Actual SDK adapter and remaining physical boundary

`sample_connected_arm(arm, now, serial=..., side=...)` reads an **already connected**
pinned Maker Arm SDK object using its real `get_joint_positions`, motor feedback,
`feedback_age`, joint limits and configured direction. It does not call refresh,
connect, enable, targets, release or disconnect. Its output includes configured
SDK `q_sdk_rad`, `dq_sdk_rad_s`, `torque_nm`, raw motor velocity/torque, six fault
states, temperature, limits and feedback timestamp. `physical_output_enabled`
means this bridge is not outputting, **not** independently verified torque-off.
The SDK profile defines motor=joint×direction+offset; direction is±1.

A later physical adapter must run on the chosen SDK host and reuse the existing
six-joint `arm/tools/lift_probe.py` guarded lifecycle. It must own CAN exclusively,
perform fresh stationary preflight and calibrated-coordinate checks, keep stock
gravity/gains and call the SDK's `set_joint_targets` only after local enable and
fresh deadman admission. On interruption or faults, retain the supervised
hold/inspection process; verified RETURN or operator-supported release must use
the existing lifecycle. Do not add `finally: disconnect()` to an elevated arm.

**Tomorrow's prerequisites:** choose/verify host and exact adapter serial/side;
read the [physical handoff](../arm/docs/opus-handoff-2026-11-07.md); obtain fresh
hardware feedback/camera views and local operator cutoff; validate the physical
SDK↔URDF and world registration; establish reliable local deadman/watchdog and
safe hold/rest behavior. Current nominal-bound discrepancies and unverified
legacy firmware watchdog remain blockers. A web checkbox is not physical enable.
No recorded simulation or successful unit test substitutes for those checks.

## Offline verification

```sh
uv run --python 3.11 --with numpy python -m unittest Interaction.test_interaction
```

Tests cover contact expiry/replay/cross-arm rejection, finite/force/frame guards,
centimetres/handedness, Jacobian finite differences and virtual-work identity,
bounded admittance, calibration refusal, zero-force hold and the actual SDK
attribute interface with a fake object that has no actuation methods. They do
not prove physical operation.

Primary SDK coordinate reference:
https://github.com/makermods-robotics/maker-arm-sdk/blob/0245316c05cb3e8d900f35e70c08e2e18e36731e/maker_arm/config.py

## Tomorrow: mirror the measured physical arm

`feedback_pose.measured_feedback_pose(state, calibration, now)` converts the six
**measured** SDK joint positions into the official URDF joint angles for Table 10.
It requires the registered serial, side, calibration ID and validated mapping;
it rejects feedback older than 250 ms, future timestamps and poses outside the
URDF limits. Commanded targets are ignored. The returned `jaw_gap_m` is `None`:
without a separately measured and calibrated gripper channel, its opening is
unknown and the renderer must not invent it. This read-only mirror needs no
physical deadman and cannot enable motors. The future UE consumer must stop
updating and visibly mark telemetry stale when this conversion rejects a sample.
The transport and UE application of these measured poses remain to be connected
on the SDK PC tomorrow; no current pose is presented as hardware telemetry.
