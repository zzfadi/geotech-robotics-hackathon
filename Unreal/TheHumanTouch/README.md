# The Human Touch — Unreal

Requires **Unreal Engine 5.8.3**, changelist **58210709**. The primary map is `/Game/Maps/TheHumanTouch` (editor startup and game default); agent presence is editor/session visualization.

## Shared human and agent editor

Run **one Unreal Editor per computer**. The person and agent use that editor together. Agent runners, voice helpers and the Multi-User server are separate processes. Do not use the original AgentCoPresence two-editor launch workflow here.

After building the native CoPresenceFab plugin for your platform, run:

```sh
uv run --script Tools/join_human_touch.py --local-address YOUR_VPN_IP --name "Your Agent Name"
```

The launcher refuses if any Unreal Editor is running, including another project. Save work and close that editor gracefully before restarting. It joins Luna2's `TheHumanTouch` server/session on UDP 9040, connects chat to `http://100.84.28.59:8791`, and serves scoped agent tools on localhost port 8041. The Agents and Chat tabs open automatically. The agent uses your shared viewport; automatic voice-companion camera following is disabled.

A runner must also be started. On Mac:

```sh
COPRESENCE_HUB_URL=http://100.84.28.59:8791 python3 Tools/copresence_runner_service.py --project "$PWD" --brain codex --port 8041
```

On Windows, supervise `uv run --script Tools/copresence_mcp.py run-agent claude --port 8041` with `COPRESENCE_HUB_URL=http://100.84.28.59:8791` and `HUMANTOUCH_SHARED_EDITOR=1`. The relevant CLI must already be signed in. A runner has no reason to open another editor.

The `+ Agent` station workflow cannot launch extra editors in this project. Additional agents belong on other computers unless the user explicitly changes the limit. Use the existing agent's **Call** button for voice; **Hang up** ends the call.

## Private configuration

Provision the session code in `Saved/CoPresenceLogs/hub_code`, host address in `Saved/CoPresenceLogs/host_address`, and voice credential reference in `Saved/CoPresence/voice.json`. Never commit them. On the managed Mac use the approved `op-agents` helper. Voice needs audio devices and OS microphone permission; a connected helper alone does not prove an audible conversation.

## Native build

Build `Plugins/CoPresenceFab/CoPresenceFab.uplugin` using UE's `RunUAT BuildPlugin`, targeting Mac or Win64. Copy the resulting platform `Binaries` into this project's plugin; generated binaries stay out of git. Optional CoPresenceAnnotate source is included but is not enabled until built. CoPresenceCall needs the engine's NDIMedia plugin. Both participants must have matching enabled plugins and project content.

## Validation

`Tools/validate_empty_map.py` loads the baseline and checks engine version and zero authored actors. The initial map was verified on Mac and Luna2. Scoped `Tools/copresence_mcp.py call session '{}'` uses `COPRESENCE_UNREAL_MCP_URL=http://127.0.0.1:8041/mcp`; verify the returned session and peer identities. Test a read-only chat request and voice independently.

Mac Codex and Luna2 Claude have both returned verified Agent Chat replies in TheHumanTouch; both avatars are visible and each machine runs one editor. On November 7 the user confirmed an audible voice call from the Mac to Luna2 Claude works. Shared-editor mode enables the human microphone/playback helper alongside the agent runner.

## Checkpoints

Luna2 checkpoints and pushes ready Unreal content to GitHub every **15 minutes**, plus completed milestones. The job defers active/unsaved work instead of committing an incomplete checkpoint. See [checkpoint operations](../../docs/checkpoints.md) and `Config/CheckpointPolicy.json`. Git commits do not clear Multi-User history: use the documented coordinated archive/fresh-session boundary to keep transaction history short.

See [migration research](../../docs/agentcopresence-migration.md) for VR and later robot work.

## Maker Arm station

The target is MakerMods Maker Arm. The shared scene's earlier I2RT YAM preview has been replaced with the official Maker Arm geometry, as recorded in the [model assessment](../../docs/maker-arm-model.md). The initial zero-actor map validator is historical after the station addition.

The **URDF (Unified Robot Description Format)** is [SourceAssets/MakerArm/robot.urdf](SourceAssets/MakerArm/robot.urdf), from the [official SDK model package](https://github.com/makermods-robotics/maker-arm-sdk/tree/0245316c05cb3e8d900f35e70c08e2e18e36731e/urdf/maker_arm). Keep `meshes/` adjacent to it. The package includes 52 STL meshes, six revolute arm joints, two prismatic jaws with a mimic relationship, and a fixed `grasp_center`. See the [upstream usage and limitations](SourceAssets/MakerArm/README.md) and [provenance manifest](SourceAssets/MakerArm/provenance.json).

### Existing Unreal workflow

- `Tools/maker_arm_model.py` reads the URDF, computes joint frames, and prepares nine combined link OBJ meshes under `Saved/CoPresenceImports/MakerArm`. Mesh preparation preserves visual origins and converts geometry to centimetres; mounted frames handle CAD Y-up and the Unreal pose conversion handles handedness.
- `Tools/import_maker_arm_meshes.py` imports those prepared meshes into `/Game/_Agents/Denys_Mac_Codex/MakerArm` through a scoped CoPresence task. It does not generate collision or save packages automatically; use the shared-editor persistence/checkpoint workflow.
- `Tools/maker_arm_station.py` positions the two arms (18 link actors). `set_pose(side, q, gap)` takes six URDF joint angles in radians and **total** jaw opening in metres, from 0 to 0.104825. It enforces symmetric jaw motion. These coordinates are not the SDK's motor angles. `replace_yam()` is a guarded migration for the old complete station, not a routine refresh command.

Run the offline kinematic checks from the repository root:

```sh
python3 -m unittest discover -s Unreal/TheHumanTouch/Tools -p 'test_maker_arm_model.py'
```

This is an editor kinematic representation, not a physics articulation. Dream-1 inference and grasp physics are not connected. Upstream dynamics and physical joint alignment remain unvalidated; collision decomposition, calibrated contacts, station registration and motor-to-jaw transmission still require validation. No runtime or asset reimport is required solely to update this documentation.
