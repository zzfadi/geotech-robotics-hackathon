# geotech-robotics-hackathon

GeoTech robotics hackathon workspace.

## Dashboard

Track the team's progress at **https://zzfadi.github.io/geotech-robotics-hackathon/**. To post an update, see [dashboard/README.md](dashboard/README.md).

## Contributing

All development takes place directly on `main`. Read [AGENTS.md](AGENTS.md) for the branch workflow, conflict-resolution guidance, and agent working rules.

## Turn-taking prototype

The [local turn gate](turn_gate/README.md) tests hand occupancy, observation freshness and explicit robot turns with a deterministic replay. The optional [MediaPipe camera preview](turn_gate/CAMERA.md) feeds hand observations into it, with keyboard turn requests and timing measurements. Neither needs robot access or API credentials. See the [robot stack integration boundary](docs/robot-stack-contract.md) before connecting Dream-1 or a physical arm.

## Unreal project

Open [TheHumanTouch.uproject](Unreal/TheHumanTouch/TheHumanTouch.uproject) with **Unreal Engine 5.8.3** (CL 58210709). The shared scene contains the official Maker Arm link geometry; the primary map is `/Game/Maps/TheHumanTouch`, configured for editor startup and game default. Calibrated physics is in progress. Three virtual cameras are present: left wrist, right wrist and central tabletop overview; wrist cameras follow their arm links. Camera calibration remains provisional. See the [project instructions](Unreal/TheHumanTouch/README.md) and [AgentCoPresence migration research](docs/agentcopresence-migration.md).

For the two-person capture demo, see the [Mac Live Link, simulated contact and Quest overlay plan](docs/performance-capture.md), including a printable table registration target.

## Target robot: MakerMods Maker Arm

The close-up from our station shows **MakerMods Maker Arm**. The earlier YAM model was a provisional identification and must not be used as the one-to-one model. Dream Labs supplies the Dream-1 policy; MakerMods supplies this hardware. Specifications checked November 7, 2026.

### Published hardware specifications

| Property | Published specification |
|---|---|
| Structure | 3D-printed frame; kit or assembled |
| Degrees of freedom | Six arm joints plus separately actuated gripper |
| Rated payload | 1.5 kg |
| Reach | 570 mm product specification; the website's workspace illustration separately labels approximately 610 mm model reach |
| Actuators | RobStride QDD: RS02 at joints 2/3; RS00 at joints 1/4/5/6 and gripper |
| Power | 48 V supply |
| Communications | CAN; SDK uses RobStride MIT protocol, standard 11-bit CAN at 1 Mbps |
| Cameras | Wrist and overhead/workspace cameras included; sensor, resolution, optics and calibration not specified on the product page |
| Emergency stop | Hardware E-stop included |
| Host connections | Three USB connections: CAN adapter and two cameras; USB4/Thunderbolt 3+ recommended |
| Supported SDK hosts | Linux SocketCAN; macOS serial SLCAN |
| Software | MakerMods Lab, Python SDK, Dream Labs web app |
| Listed policies | ACT, SmolVLA, π0, Dream-1 |
| Optional leader | Star Arm 102-LD |

Sources: [MakerMods product specifications](https://www.makermods.ai/maker-arm), [official SDK at our pinned revision](https://github.com/makermods-robotics/maker-arm-sdk/tree/0245316c05cb3e8d900f35e70c08e2e18e36731e), [Dream Labs](https://dreamlabs.ai/). Payload and reach are manufacturer claims, not measurements of our station. Do not apply Metal Arm's 3 kg / ±0.1 mm specifications to Maker Arm.

### SDK control profile (not URDF joint coordinates)

The pinned [Maker Arm v1 profile](https://github.com/makermods-robotics/maker-arm-sdk/blob/0245316c05cb3e8d900f35e70c08e2e18e36731e/maker_arm/profiles/maker_arm_v1.yaml) defines the following **motor-coordinate** bounds after zeroing in the folded, closed-gripper pose. They are not interchangeable with the designer URDF angles.

| CAN ID | Joint | Motor | Lower / upper (degrees) | Kp / Kd |
|---|---|---|---|---|
| 1 | Shoulder pan | RS00 | −158.2 / 156.1 | 60 / 4.0 |
| 2 | Shoulder lift | RS02 | −175.3 / −3.2 | 150 / 4.5 |
| 3 | Elbow flex | RS02 | 2.8 / 236.1 | 90 / 3.0 |
| 4 | Wrist flex | RS00 | −65.2 / 104.0 | 30 / 2.0 |
| 5 | Wrist yaw | RS00 | −97.6 / 78.0 | 30 / 2.0 |
| 6 | Wrist roll | RS00 | −153.2 / 152.0 | 30 / 2.0 |
| 7 | Gripper | RS00 | −96.0 / −2.5 | 20 / 0.5 |

The profile requests 200 Hz and a 5 rad/s command-rate limit; the README documents verified macOS SLCAN updates at 25 Hz. These are software settings/transport behavior, not a guaranteed arm bandwidth. Feedback timeout and fault poll period are 250 ms. Gripper closing torque is capped at 1 Nm, with 0.5 Nm contact hold; stop temperature is 55°C and enable threshold 45°C. Its 500 ms watchdog requires compatible RS00 firmware. Joints 1–6 lack that watchdog in this SDK. These values describe upstream behavior; no hardware command has been issued by this project.

### Official simulation model and fidelity

**URDF means Unified Robot Description Format.** It is the XML description of links, joints, geometry and physical parameters. MakerMods links this package from its [Maker Arm product page](https://www.makermods.ai/maker-arm).

- [Official SDK](https://github.com/makermods-robotics/maker-arm-sdk/tree/main) and [current upstream URDF package](https://github.com/makermods-robotics/maker-arm-sdk/tree/main/urdf/maker_arm).
- [Local robot.urdf](Unreal/TheHumanTouch/SourceAssets/MakerArm/robot.urdf): keep the adjacent `meshes/` directory when copying or importing.
- [Provenance and file hashes](Unreal/TheHumanTouch/SourceAssets/MakerArm/provenance.json) and [Unreal model workflow](Unreal/TheHumanTouch/README.md#maker-arm-station).

The [official URDF + meshes](https://github.com/makermods-robotics/maker-arm-sdk/tree/0245316c05cb3e8d900f35e70c08e2e18e36731e/urdf/maker_arm) are vendored unmodified in [SourceAssets/MakerArm](Unreal/TheHumanTouch/SourceAssets/MakerArm), with license, source commit and per-file hashes. Entry point: `robot.urdf`. It contains 52 STL files, six revolute joints, two symmetric prismatic jaws (one mimic), and a fixed grasp-center frame. Total model jaw opening: 0–104.825 mm. STL scale is 0.001 into URDF metres; original CAD is Y-up.

| Designer URDF arm joint | Position range (radians) |
|---|---|
| link_002_joint | −2.8 to 2.8 |
| link_003_joint | −3.14 to 0 |
| link_004_joint | 0 to 3.14 |
| link_005_joint | −1.6 to 1.73 |
| link_006_joint | −1.57 to 1.57 |
| link_007_joint | −2.7 to 2.7 |

The URDF assigns all six arm joints effort 10 and velocity 5, but upstream marks these inherited limits unverified. It assigns each jaw 0–0.0524125 m travel, effort 20 N and speed 0.1 m/s; the latter two are explicit placeholders. Mass, COM, inertia and friction fields are included, but some are fitted or estimated. Internal gripper drive parts are omitted; finger-loop visuals are removed while their collision/mass remain. Part_003 is unresolved in the source export.

**One-to-one implementation remains in progress.** Matching the official geometry and kinematics is the starting point. Exact physical fidelity requires our unit's hardware revision, encoder-to-CAD mapping, measured jaw transmission, link dynamics, contact properties, base registration and camera calibration. The manufacturer does not validate this model for RL dynamics. No public native STEP assembly, guaranteed Maker Arm repeatability, measured full-arm mass/inertia set, or camera calibration was found in the reviewed sources. See [model assessment and validation plan](docs/maker-arm-model.md). The YAM arm actors have been replaced with the official Maker Arm meshes in both live editors; physics and hardware calibration remain incomplete.
