# Table 10: virtual-to-real SDK connection

## Objective and current boundary

The requested end state is a virtual human hand or moving object touching a Maker Arm in Unreal and producing a bounded response on the real arm, with measured hardware state returned to Unreal. Table 10 is separate from the nine Dream cup stations.

On November 7, the user selected a Jetson Nano as the planned robot server: both arms will connect to that machine, which will own the SDK connection and local motor lifecycle. The server is not started or connected yet; its network address, exact Jetson model, Linux/JetPack version and adapter assignments remain unverified. The user asked us to be ready when they start it. No physical connection or powered test is claimed. The current work prepares the scene, authenticated telemetry relay, and offline control calculations. It is not an operational force-coupled digital twin.

Table 10 is saved in Unreal checkpoint `adf8fee`. The measured-feedback conversion in `Interaction/feedback_pose.py` accepts fresh encoder positions with validated arm identity and SDK-to-URDF calibration; it ignores requested targets and leaves jaw gap unknown. It is not yet a live Unreal polling/mirroring loop.

## Web relay

The existing Luna2 panel includes a dedicated Table 10 section and a latched **Stop interaction requests** control. This is a network request, not a physical emergency stop. The browser cannot enable motors.

Routes:

- `GET /api/interaction/status`: current controller presence, selected arm, measured feedback and fresh contact.
- `POST /api/interaction/controller/session`: controller role; host and explicit left/right side; returns a fresh session nonce.
- `POST /api/interaction/controller/state`: controller role; session, side, mode, local deadman state, calibration ID, six SDK joint positions/velocities/efforts/temperatures/faults and feedback timestamp.
- `POST /api/interaction/contact`: producer role; session, side, table 10, hand/object identity, sequence, link, right-handed URDF-base contact point in metres and force in newtons.
- `GET /api/interaction/controller/contact?session_id=...`: controller role; latest valid contact only. No hardware-enable instruction is provided.
- `POST /api/interaction/stop`: latches stop and clears the current contact. Heartbeats cannot clear it. A new locally initiated controller session is required after the old lease expires.

Separate controller and producer bearer credentials are generated in ignored `Saved/DreamDebugger/interaction-controller-token` and `interaction-producer-token`. They never go into the web page or Git. Use the existing private HTTPS/Tailscale route. Do not expose this service publicly.

Contacts expire after 100 ms, old sequences and session nonces are rejected, and controller feedback must be no older than 250 ms. Clocks must be synchronized; future timestamps are rejected. The relay rejects forces above 5 N and contact points outside a 2 m base-centred envelope. These are experimental software gates, not certified hardware safety limits. A receiver must independently enforce expiry and its own local enable and feedback guards.

## Remaining end-to-end work

1. Connect to the user-started Jetson Nano server and confirm its exact platform, Python version, physical arm/adapter identity. Inspect fresh feedback using the existing bench tools without competing CAN clients.
2. Establish and validate UE-world-to-base and SDK-motor-to-URDF mappings. The existing handoff explicitly describes those mappings as provisional.
3. Connect real UE hand/object contact measurements, including the correct arm side, to the producer. Moving a visual mesh is not a measured contact force.
4. Integrate the bounded control proposal into the existing SDK return-or-hold lifecycle with an operator-held local enable. Do not replace it with an unconditional torque release or replay arbitrary remote motor targets.
5. Verify stale-contact, lost-network, wrong-arm, fault and local-enable-release behavior with hardware output disabled first.
6. With the station attended and a physical cutoff available, perform a bounded powered interaction and record synchronized UE contact, controller targets and actual encoder motion. Verify real feedback drives the matching virtual arm.

The physical objective remains incomplete until the last step is demonstrated. Nine-table Dream recordings or an offline test cannot prove Table 10 hardware interaction.

## Jetson readiness

Keep Luna2 as the web/relay host and Unreal on the existing Mac/Luna2 editors. Do not start a second SDK/CAN owner automatically. The pinned [MakerMods SDK metadata](https://github.com/makermods-robotics/maker-arm-sdk/blob/0245316c05cb3e8d900f35e70c08e2e18e36731e/pyproject.toml) requires Python 3.10 or newer; verify the Jetson environment before choosing an installation route. No Jetson software installation, device connection, or motor command has been attempted.

When the user starts the server, obtain its reachable hostname/address and authorized access, inspect the environment, then begin with measured-feedback-only mirroring. The current relay supports one explicitly selected arm per session; simultaneous two-arm telemetry/control still requires extending that contract. Do not claim that both physical arms are already connected or mirrored.
