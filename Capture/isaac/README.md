# Stream the station's Live Link Hub poses to Isaac Sim

Run one exporter in the **station Mac editor connected to Hub**. It reads the three configured subjects, `Human_Quest_A`, `Human_Cam_B` and `Human_Quest_C`, after the editor updates its enabled Live Link Hub source. The exported transforms are the editor's evaluated skeleton before MetaHuman retargeting. An evaluated transition can blend two sensor poses. [Epic Live Link Hub clients](https://dev.epicgames.com/documentation/en-us/unreal-engine/live-link-hub-quick-start-in-unreal-engine) · [Epic Live Link evaluation](https://dev.epicgames.com/documentation/unreal-engine/live-link-in-unreal-engine)

The wire type is `hub_evaluated_pose_v2`. Each packet includes two identities.

- `evaluation` contains the exporter session, Live Link callback tick and station monotonic read time. All subjects read in one callback share this identity. The Isaac receiver schedules poses using this time. Two evaluations can differ even when their capture references are identical.
- `capture_ref` contains the upstream session, sequence and capture timestamp carried in Live Link's metadata. It identifies the producer frame to compare when testing correspondence. Only comparison against an independently retained source frame can certify a numerical source match. The packet also contains the reported registration space, the bone hierarchy, component-space positions and rotations, and confidence.

Upgrade the exporter, gateway and Isaac receiver together. The v2 receiver rejects the ambiguous v1 export.

```text
station webcam and Quest 3S ──┐
second Mac / Quest 2 ─────────┴─ station Live Link Hub ── station Unreal client ── MetaHumans
                                                               └─ HubPoseExport
                                                                   loopback UDP 17866
                                                                     ↓
                                                               station gateway
                                                                     ↑ TCP 17867
                                                               Luna2 connects over Tailscale
                                                                     ↓
                                                               Isaac pre-physics callback
```

UDP 17861 continues to carry the local producer copy for the existing external simulation. This route uses a separate port for frames evaluated downstream of Hub. Publication follows the station editor's Live Link tick. A faster source can produce more frames than the editor evaluates.

## Station Mac

1. Build the optional module with the **same UE 5.8.3 build** as the station editor. From the repository root, these commands create a separate build host under the project's ignored Saved directory.

   ```sh
   engine="/Users/Shared/Epic Games/UE_5.8/Engine"
   work="$PWD/Unreal/TheHumanTouch/Saved/IsaacHubExportBuild"
   mkdir -p "$work/Plugins/HubPoseExport"
   cp -R Capture/isaac/HubPoseExport/. "$work/Plugins/HubPoseExport/"
   echo '{"FileVersion":3,"Plugins":[{"Name":"HubPoseExport","Enabled":true}]}' > "$work/ExportBuild.uproject"
   "$engine/Build/BatchFiles/Mac/Build.sh" UnrealEditor Mac Development \
     -Project="$work/ExportBuild.uproject" -architecture=arm64 -NoHotReload -NoUBA -NoUBALocal
   ```

2. On the capture editor's next normal launch, add `-PLUGIN=<absolute path to work>/Plugins/HubPoseExport/HubPoseExport.uplugin` and **append** `HubPoseExport` to its existing comma-separated `-EnablePlugins=` value. Keep the normal project, XR and Multi-User arguments. Start the gateway with Python 3.11+ and use the actual Tailscale addresses.

   ```sh
   python3 Capture/isaac/pose_stream.py serve --bind "$STATION_TAILSCALE_IP" --peer "$LUNA_TAILSCALE_IP"
   ```

3. Run `HumanTouch.Isaac.Start` in that editor's Unreal console. The gateway binds the UDP receiver to loopback and the TCP listener to the specified station interface. It permits one connection from the specified peer. Large skeletons travel over TCP on the WAN. The gateway coalesces unsent frames per performer and the receiver expires buffered poses. `HumanTouch.Isaac.Stop` stops the native export.

## Luna2

With the same checkout on Luna2, first check the transport without starting or interrupting Isaac. In PowerShell, set `$station` to the station Mac's Tailscale IPv4 address.

```powershell
py -3.11 Capture/isaac/pose_stream.py observe --station $station
```

Move one performer and check that its `evaluation.tick` advances on Luna. Compare its `capture_ref` to the station editor's `HumanTouch.Pose.Inspect` output for the same short rehearsal. This checks metadata transport. Test geometric correspondence separately with the procedure below. Disconnect the observer with Ctrl+C before attaching Isaac. A route that answers Tailscale ping still needs application connectivity to TCP 17867 on the **station Mac**.

In an existing initialized, running Isaac simulation that owns the Maker Arm scene, run the following in its Script Editor or call the same constructor from its Python entry point. Use the local repository path, the actual calibrated space ID and a USD parent whose local frame matches the surveyed station frame. This adapter expects a Z-up stage. The wire convention is right-handed metres with +X forward, +Y left and +Z up. The preview reads the stage's metres-per-unit setting.

```python
import sys
sys.path.insert(0, r"<repository>\Capture\isaac")
import omni.usd
from isaacsim.core.api.simulation_context import SimulationContext
from isaac_preview import IsaacPreview

context = SimulationContext.instance()
preview = IsaacPreview(context, omni.usd.get_context().get_stage(),
                       station="<station Tailscale IPv4>", space="table_v1",
                       parent="/World", delay_ms=100)
# After moving a performer:
print(preview.receiver.status, preview.applied)
# Before closing or replacing the stage:
# preview.close()
```

The callback plots confident bones as USD curves in an anonymous session layer and records the evaluated sample identity and its capture reference for each simulation tick. `preview.close()` removes the callback, preview layer and network connection. If the scene uses a different simulation API, call `PoseReceiver.before_step(space)` from its existing **pre-physics** callback and close the receiver when the stage closes. The Isaac Sim 5.1 and 6.1 legacy `SimulationContext.add_physics_callback` interfaces document this hook. [NVIDIA 5.1 physics callbacks](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/introduction/quickstart_isaacsim_robot.html) · [NVIDIA 6.1 SimulationContext API](https://docs.isaacsim.omniverse.nvidia.com/6.1.0/py/source/deprecated/isaacsim.core.api/docs/index.html)

### Physics timing and acceptance

- Luna exchanges four monotonic timestamps with the station. The smallest recent round-trip sample estimates the offset between the machines. Network path asymmetry contributes clock uncertainty. For each physics tick the receiver converts Luna time to station time and subtracts the playout delay. Every subject uses the latest editor evaluation at or before that common target. The default 100 ms is a starting value to measure under demo load.
- A frame older than 250 ms drops out. A disconnect discards previous data and clock estimates. Both the evaluation and its capture reference must be fresh. A new editor read of a disconnected subject still expires. The stage adapter also withholds hip-relative or unregistered tracking and hides the skeleton on expiry. The observer can still inspect unregistered data by its space ID.
- To drive contact, feed the selected sample to the robot owner's **kinematic colliders before the same physics step**. The supplied curves are visual markers. The robot owner must validate collision geometry, confidence, reacquisition and the stage-to-station transform. To synchronize Unreal playback of **Isaac results**, return `(simulation tick, target_ns, subject, evaluation.session, evaluation.tick, capture_ref)` with robot state and render corresponding poses together. A low-latency local headset and a buffered LA simulation can display different sample ages.
- Verify three independent IDs, measured reach against a surveyed station point, stale withdrawal, a paused/resumed timeline, and capture-to-applied age over an actual shared-table run. Tailscale RTT measures the tunnel. The physical capture-to-physics latency includes acquisition, Hub, editor ticks, playout, simulation scheduling and any return path.

### Verify source correspondence during motion

UE 5.8.3 builds a subject snapshot before dispatching `OnLiveLinkTicked`. Its `EvaluateFrame_AnyThread` API returns that snapshot. In Engine Time mode an interpolation processor can combine transforms and retain metadata from a neighbouring frame. `RegisterForSubjectFrames` on the recipient can expose discrete received frames, but upstream Hub rebroadcast can itself use evaluated data. Switching to Latest at the station editor alone cannot establish original sensor identity.

For a short moving rehearsal, retain original producer datagrams and received v2 packets as two local newline-delimited JSON files. Include motion onset, reversal and return to rest for each subject. Record frames from the same session and station clock. The verifier pairs by subject, space, source session, sequence and timestamp, checks names and parents, converts the original coordinate frame, then compares every joint's position, quaternion (including equivalent signs) and confidence. It reports `matched`, `different` or `missing_witness`. Its match tolerances are 0.01 mm, 0.01° and 0.00001 confidence.

```sh
python3 Capture/isaac/verify_pose_identity.py producer.jsonl evaluated.jsonl
```

The command exits nonzero for a geometry difference, missing witness or empty capture. A zero result applies only to the evaluated records in the file. `compare_capture(original, evaluated)` supports the same check inside a live rehearsal harness. If the robot needs the visibly blended motion, use the `evaluation` identity throughout. To reproduce **original** samples, use discrete unmodified Hub forwarding and Latest evaluation on the client, then verify the moving run against an independent producer witness.

### Local checks

```sh
python3 -m unittest discover -s Capture/isaac -p 'test_pose_stream.py'
uv run --python 3.12 --with usd-core python -m unittest discover -s Capture/isaac
```

The tests exercise three synthetic subjects, cross-host clock epochs, stale withdrawal and USD preview cleanup. A transition regression keeps the same capture reference for two editor reads whose positions differ by 0.499 m. They have distinct evaluation identities, and the verifier rejects the displaced pose as an original-frame match. The earlier v1 station smoke test exposed this ambiguity in actual Unreal output. The v2 exporter was also tested on the station Mac with three synthetic subjects flowing through the running Hub and Unreal editor. In one moving rehearsal the loopback receiver audited 249 distinct evaluations. Of those, 235 matched the referenced producer frame across every joint and 14 differed during transitions. A second run audited 35 evaluations across all three subjects after an editor and Hub restart. Acceptance on Luna2 still requires a real application connection, a calibrated space and readback from Isaac’s physics callback.

### Measure Unreal-to-Isaac delay on a physics tick

Start the native exporter with `HumanTouch.Isaac.Start` in the Hub-connected editor. With the hardware publishers idle, run the audit gateway from the checkout on the station. Use a fresh trial name and an unused output filename. The station generates all three moving subjects locally and writes a SHA-256 digest of each evaluated Unreal packet. After a short preflight it waits for the configured Luna peer before continuing to publish.

```sh
python3 Capture/isaac/bridge_trial.py station --bind "$STATION_TAILSCALE_IP" --luna "$LUNA_TAILSCALE_IP" --trial "$TRIAL" --output unreal.jsonl
```

Before launching Isaac, check the current editor log for `LogHubPoseExport: Display: Exporting enabled Live Link Hub performers` and confirm the preflight wrote records for all three subjects. An editor restart creates a new evaluation session. If it happens between preflight and the remote run, enable the plugin in the new editor, start its exporter, and repeat preflight. The station gateway can keep listening after its editor has stopped exporting.

On Luna, use the installed Isaac Sim Python environment to run the following command in the same checkout. The script creates a temporary Z-up physics world, registers the bridge with its pre-physics callback, steps the simulation, and writes each subject’s first application of a distinct evaluation. The default playout delay is 100 ms. The temporary world has no robot controller or hardware connection.

```powershell
& $IsaacPython Capture/isaac/luna_trial.py --station $StationIP --trial $Trial --output isaac.jsonl
```

Bring the two JSONL files to the same machine and run the report. It checks the complete packet digest against Unreal and checks the applied USD link endpoints on the Isaac stage. It reports capture-to-application and Unreal-evaluation-to-application median and p95 per subject. Cross-machine times are estimates using the lowest recent clock-probe RTT. Asymmetric network delay can bias the estimate by up to about half that RTT. Stop the exporter with `HumanTouch.Isaac.Stop` when finished.

```sh
python3 Capture/isaac/bridge_trial.py report --unreal unreal.jsonl --isaac isaac.jsonl
```

### Measure contact with a simulated Maker Arm

- [ ] **Sunday, November 8, 2026 — Run the live hand-to-arm contact test on Luna2.** With Luna2 PowerShell open, start a fresh station trial, verify current Unreal hand evaluations, then run the contact launcher. Record repeated hand–wrist impulses, wrist displacement relative to the clear-hand control, and the median and range of the Unreal-evaluation-to-first-impulse delay. Publish the result and restore Tailscale Shields Up. The November 7 pose-parity run verified pose application; the contact test is still pending.

Use a fresh trial identifier and an empty trial directory visible to both machines, or relay the target JSON to the Mac using the receiver's `--target-url` option. Start `HumanTouch.Isaac.Start` in the running Hub-connected editor, then start the station with the shared target file:

```sh
python3 Capture/isaac/bridge_trial.py station --bind "$STATION_TAILSCALE_IP" --luna "$LUNA_TAILSCALE_IP" --trial "$TRIAL" --contact-target "$DIR/target.json" --output "$DIR/unreal.jsonl"
```

From Luna2's Isaac Python, run:

```powershell
& $IsaacPython Capture/isaac/maker_contact.py --station $StationIP --trial $Trial --target-file "$Dir/target.json" --output "$Dir/physics.jsonl"
```

Luna2 opens the metre-scale parity scene in a separate process and puts the experimental collider in the USD session layer. It returns the settled wrist collider's center to the Mac. The Mac places the webcam performer's left hand 22 cm clear, sweeps it into the wrist, then withdraws. The hand's 3.5 cm sphere follows the Live Link pose as a PhysX kinematic target. The receipt records the solver impulse, the wrist transform and the Unreal evaluation that positioned the hand for that step.

Run the verifier on the two logs:

```sh
python3 Capture/isaac/verify_maker_contact.py "$DIR/unreal.jsonl" "$DIR/physics.jsonl"
```

It checks exact station pose hashes, absence of hand–wrist impulses during the clear controls, and wrist translation above baseline drift on repeated approaches. Delay is measured from Unreal evaluating the driving hand pose to the first positive contact impulse reported after a physics step. It includes the 100 ms receiver buffer. Scene dimensions, drive gains and the synthetic hand radius determine the contact behavior.
