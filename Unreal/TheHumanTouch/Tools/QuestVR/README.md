# Quest VR on the Mac editor, and voice control of Table 10's arms

Wear a Quest 2 in the Mac's TheHumanTouch editor (editor VR Mode, streamed over USB by OXRSys),
grab Table 10's Maker Arms by one wrist ball each, and ask the in-editor agent by voice to
pick up, move, hand over and put down the can. The real arms follow when their service is in drive.

## Files here

| File | What it does |
|---|---|
| `join_vr.py` | Relaunches the one editor with the VR plugins and the OXRSys runtime, does `adb reverse` and starts the Quest app. Copies the three helpers below into `Saved/XR` and, once the editor has joined the session, starts them. |
| `editor_vr_state.py` | Toolset `Temp.XR.editor_vr_state.EditorVRState`: whether the editor is in VR Mode. `Tools/copresence_mcp.py` asks it before every agent viewport image, because `CaptureViewport` fails an assertion in VR Mode and crashes UE 5.8.3. |
| `table10_vr_grab.py` | One grab ball per arm (`MakerInteraction_T09__ArmHandle_left/right`) on the wrist. A drag poses the whole arm by IK inside the Maker Arm limits, base fixed, 5° per tick. Other arm parts can't be selected. It leaves an arm alone unless its real-arm service is in `drive` (`Saved/RealArmLink/<side>/real_state.json`). Re-imports the can model from `SourceAssets/FreeWaveCan` if its meshes are missing (agents can't save). |
| `table10_robot_tools.py` | Toolset `Temp.XR.table10_robot_tools.Table10RobotTools`, behind the agents' `robot_arms` MCP tool: Dream tasks (through the Table 10 Dream runner), planned pickup with either arm, handover between the arms, straight-line moves (one arm or both), gripper, careful put-down (lowered onto the table before the jaws open), home. All motion goes through `Tools/maker_arm_table10_dream.py`, which carries the can and caps joints at 30°/s. Requests during a motion are queued. |

## Voice commands that work

"Pick up the can with the left arm", "use the DreamLab API to grab the bottle", "move it 10 cm up",
"move both arms up 5 cm", "give it to the other arm", "put it back on the table", "go to idle".
The agent finds `robot_arms` by itself (a robot hint is added to its turn when a message sounds like
a robot request). Keep the call closed while the real arms are in drive and you aren't commanding:
room conversation reaches the agent.

## Local-only pieces (not in Git)

- `Saved/XR/OpenXR-OSX`: the OXRSys runtime, built from Geodesic-Games/OpenXR-OSX.
- `Saved/XR/platform-tools` (adb) and `Saved/XR/adb-home` (the key the headset authorized).
- The OXRSys Quest app (`Saved/XR/app-release.apk`, built with the Android SDK in `Saved/XR/android`).
- Plugins `OpenXRMetal`, `CoPresenceVR`, `CoPresenceScouting` (from AgentCoPresence) and a copy of
  the engine's `VirtualScouting` with Mac added, built for arm64 with `Build.sh -NoUBA` in a staged
  host project. Binaries built there carry relative rpaths; rewrite them to absolute engine paths with
  `install_name_tool` and re-sign with `codesign -f -s -`, or the editor exits at load.

## Run

```sh
uv run --quiet --script Tools/QuestVR/join_vr.py --local-address <this Mac's Tailscale IPv4>
```

Set the Quest boundary with the OXRSys app closed first, then press VR Mode in the editor.
Never call `VPScoutingSubsystem.get_active_editor_vr_controllers()`: it crashed the editor in VR Mode.
