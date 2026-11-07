# AgentCoPresence migration research

Research date: 2026-11-07. Source checkout: Geodesic-Games/AgentCoPresence, inspected HEAD `852ba2841db6aa1a3d8c53b4cf1e33b8c623e88e` plus working-tree files. This is a reuse plan, not a claim of working integrations in the new project.

## Immediate deliverable

UE **5.8.3**, CL **58210709**, a separate `Unreal/TheHumanTouch` project and one empty `/Game/Maps/Empty` level. No robot, table, avatar, hotel, office or festival scene is required now. All participants must use the same engine build and project assets. Both Mac and Luna2 Build.version files matched this build.

## Reuse inventory

| Capability | Existing implementation | Migration work / dependencies |
| --- | --- | --- |
| Visible agents, tracked tasks, chat | `Plugins/CoPresence`, `Tools/copresence_mcp.py` | Python startup, ghost and UI assets; MultiUserClient, PythonScriptPlugin, EditorScriptingUtilities, ToolsetRegistry, EditorToolset, ModelContextProtocol, WebBrowserWidget, SlateInspectorToolset |
| Multi-user authoring | Unreal Concert/MultiUserClient; host and join scripts | Dedicated HumanTouch session, same content revision, reachable transport endpoints; do not join the existing hotel session by default |
| Multiple agents | `Tools/copresence_station.py`, per-agent join lifecycle and runner | Separate editor process, identity, user directory and MCP port per agent; authenticated hub; machine-local Claude/Codex login |
| Spoken agent conversations | `copresence_voice.py`, `copresence_agent_voice.py`, `copresence_voice_core.py`, `CoPresenceCall` | Microphone/output routing, machine-local voice credentials, agent-specific call routing; validate speech round trip independently of text chat |
| Caption voice commands | `copresence_voice_commands.py`, `copresence_meet_input.py` | Explicit wake/command turns, speaker isolation, expiry and deduplication; this is caption-backed, not audio VAD; Meet integration is optional |
| Human VR presence | `CoPresenceVR` | OpenXR, EnhancedInput, MultiUserClient, ProceduralMeshComponent; replicated head/hands and controller annotations; native Win64 module build |
| VR agent selection/talk/captions | `CoPresenceScouting` | VirtualScouting, XRCreativeFramework, CommonUI, CoPresenceVR, EnhancedInput, PythonScriptPlugin; validate on Quest 3S PCVR |
| Desktop annotations | `CoPresenceAnnotate` | Native build and editor overlay; useful for pointing at targets |
| Asset sharing / save ownership | `CoPresenceFab` and sharing tools | Preserve host ownership of persistence and per-agent tracked edits; do not remove guards to make integration easier |
| Human performance input | LiveLink / LiveLink Hub | Capture provider, calibration, subject names and retargeting are separate work; enabling LiveLink does not create webcam skeleton tracking |
| Calls / streaming | `copresence_call.py` and call voice tools | Optional later; existing Meet lifecycle assumes AgentCoPresence and requires adaptation |

## Isolation changes before importing core

The current `Plugins/CoPresence/Content/Python/init_unreal.py` registers core tools but also starts roomrunner, festival and scan-robot modules, with some behavior keyed to an agent name. Extract a project-neutral startup rather than copying those side effects. Audit registration and imports for scene dependencies as well as the top-level startup.

Mac `join-mac.sh` accepts `COPRESENCE_PROJECT_FILE`; verify the Windows counterpart and all helpers before reusing them. Remove default map/project assumptions. The original hub uses 8790 and first agent MCP uses 8000; station agents use separate user folders and ports (README gives 8102, 8103, ...). Choose and verify unused ports for this independent project; session naming alone does not isolate local listeners. Keep session codes and credentials outside git. Do not copy `Saved`, `AgentUser`, logs, browser profiles or caches.

Use source-built plugins for UE 5.8.3 on each platform. Do not copy Mac binaries to Windows. The initial engine-only project can open independently while this migration is completed. Quest 3S input is an intended target, not a verified headset connection. OpenXRMetal is a Mac-specific path and is not a prerequisite for Windows Quest PCVR.

## Proposed acceptance sequence

1. Open and reload the single empty map on Mac and Luna2 in 5.8.3.
2. Build and enable the selected neutral CoPresence plugins; confirm startup has no scene-specific side effects.
3. Host a separate session and connect two editors at the same revision; verify a temporary actor transform reaches the peer, then remove it so the baseline stays empty.
4. Add two distinct agent editors through the adapted join/station lifecycle; verify identities, scoped task tracking and chat routing independently.
5. Verify a microphone request reaches only the selected agent, response audio returns, and cancel/repeated captions do not create duplicate commands.
6. Connect Quest 3S through the configured OpenXR runtime; verify head/hands, peer presence, agent selection and voice. This requires the actual headset and operator.

## Hackathon context and later robot work

[Slack brief and Granola discussion](https://geotech-org.slack.com/archives/C0CFR1X1W2E/p1794079102958319) describes The Human Touch: human-robot interaction training with Unreal visualization/XR, Isaac simulation, and possible DreamLab integration. The direct Granola share URLs failed to load in this session; the Slack-posted summary and discussion were read, not the full original notes. Oracle's proposed tabletop turn-taking example is a proposal rather than a confirmed product decision.

The two user-supplied images show tabletop-mounted articulated arms with two-finger grippers, external camera equipment and a physical stop control. Slack identifies the intended hardware as MakerMods Maker Arm. Photos do not establish joint count, dimensions, limits, payload or SDK version: obtain vendor CAD/URDF, joint order and limits, coordinate conventions and feedback interface before building the digital twin. Keep the level empty for this milestone.

For later integration, specify timestamped observations, frame IDs, units and confidence. Convert UE centimetres and axes explicitly to robotics coordinates. Give simulated physics one authority rather than independently simulating the same object in Unreal and Isaac. Keep physical actuation behind the hardware owner's controller; the present project sends no hardware commands. VR controller poses are tracked points, not a full-body ground-truth skeleton.
