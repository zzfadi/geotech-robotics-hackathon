# Run table performance capture

The external producers send each frame to two loopback UDP sockets. Live Link Hub reads port **17860** and the external robot process reads **17861**. Both receive the same skeleton, source identity and monotonic station timestamp. The capture tools run locally. Dream-1 can supply actions to the external robot application. Its hardware integration is described in the [robot stack contract](../docs/robot-stack-contract.md).

## Mac editor and Hub

1. Build [HumanTouchPose](../Unreal/TheHumanTouch/Plugins/HumanTouchPose) with the project's UE 5.8.3 installation. Build the local editor target and copy the generated module manifest and dylib into the plugin's ignored `Binaries/Mac/` folder. From the repository root, one way to build on Apple Silicon is:

   ```sh
   engine="/Users/Shared/Epic Games/UE_5.8/Engine"
   plugin="$PWD/Unreal/TheHumanTouch/Plugins/HumanTouchPose"
   work=$(mktemp -d)
   mkdir -p "$work/Plugins/HumanTouchPose"
   cp -R "$plugin/Source" "$plugin/HumanTouchPose.uplugin" "$work/Plugins/HumanTouchPose/"
   echo '{"FileVersion":3,"Plugins":[{"Name":"HumanTouchPose","Enabled":true}]}' > "$work/CaptureBuild.uproject"
   "$engine/Build/BatchFiles/Mac/Build.sh" UnrealEditor Mac Development \
     -Project="$work/CaptureBuild.uproject" -Plugin="$work/Plugins/HumanTouchPose/HumanTouchPose.uplugin" \
     -architecture=arm64 -NoHotReload -NoUBA -NoUBALocal
   mkdir -p "$plugin/Binaries/Mac"
   cp "$work/Plugins/HumanTouchPose/Binaries/Mac/"{UnrealEditor.modules,libUnrealEditor-HumanTouchPose.dylib} "$plugin/Binaries/Mac/"
   ```

2. Start one Hub with the station receiver. If Hub is already running, save any Hub session before quitting and reopening it with these arguments. `Listen` and the **Add → Human Touch Pose** menu item start the same receiver; use one.

   ```sh
   open -g "$engine/Binaries/Mac/LiveLinkHub.app" --args \
     "-PLUGIN=$plugin/HumanTouchPose.uplugin" -EnablePlugins=HumanTouchPose \
     -ExecCmds=HumanTouch.Pose.Listen
   ```

3. Use the existing shared project editor. The project enables Epic's built-in **Live Link Hub** plugin for editor targets. At the next normal launch, `Tools/join_human_touch.py --capture-tools` additionally loads the optional local drawing/inspection module (keep your usual join arguments and XR plugins). Confirm this editor in Hub's **Clients**. If the VPN-bound editor misses discovery, add the Hub's reachable **UDP Messaging unicast** endpoint to the editor's static endpoints while retaining the Concert endpoint; the Hub log prints its bound port. Port 17860 is for producer input only.
4. In the Unreal console, enter `HumanTouch.Pose.Preview 1` to draw a cyan webcam skeleton and orange Quest skeleton. `HumanTouch.Pose.Inspect` evaluates the subjects and logs their exact session, sequence and station time in the editor Output Log. The drawings are transient; `HumanTouch.Pose.Preview 0` hides them. For a rigged character, use the Animation-role subject with a matching retarget mapping.
5. To use direct Live Link, close the Hub receiver and use **Add → Human Touch Pose** in the editor with the local plugin enabled. Only one process may own the input socket.

## Person B: single webcam

Open [start_webcam.command](start_webcam.command) from Terminal or Finder. The launcher checks the SHA-256 of Google's Pose Landmarker Lite v1, caches it, and runs the external detector. The opening app needs macOS camera permission. Open **System Settings → Privacy & Security → Camera** and enable Terminal if running from Terminal. Press Control-C to stop. All image processing stays in the capture process.

The solver produces a 23-bone torso, head, arms, hand endpoints and legs. Joint confidence is available as Live Link curves named `<bone>_confidence`. Frame the entire performer from head to feet to capture hip, knee, ankle and toe motion; crop to the second performer's part of the frame with `--roi x0 y0 x1 y1` in normalized image coordinates. The source skips frames when it detects zero or multiple people inside that region. Rehearse crossings; the selected ROI itself does not identify a particular person. A still-image smoke test uses `uv run --script Capture/webcam.py --model /path/to/model.task --image /path/to/person.jpg --frames 60`.

Without calibration the webcam subject is labelled `hip_relative`. The preview lifts it 1 m above the origin; this offset is purely visual. For a table-relative solve, pass `--calibration /path/to/camera.json`. Supply `image_size` as [width,height], the 3×3 pixel camera matrix `K`, OpenCV `distortion` coefficients, a measured 4×4 rigid `table_from_cv_camera` in metres, a positive `max_pixel_error` threshold, and a versioned `space` name shared with the headset. Use a right-handed table frame with Y up. Validate held-out reaches at several heights before enabling 3D contacts. [Calibration and rehearsal](../docs/performance-capture.md).

## Person A: Quest 3 or 3S

The USB route uses one immersive Quest Browser WebXR session. It requests the experimental body-tracking feature, reads the headset's viewer pose and upper-body/finger joint transforms, and streams them to the Mac. This browser producer also provides a quick hardware check before building a native Meta Movement application with the registered robot overlay.

```sh
uv run --script Capture/quest_bridge.py
# In a second terminal:
adb reverse tcp:17862 tcp:17862
adb shell am start -a android.intent.action.VIEW -d http://localhost:17862/ -p com.oculus.browser
```

Accept USB debugging on the headset and select **Start body capture**. Put the controllers down and enable hand tracking. If the browser reports that body-tracking is unsupported, check the WebXR Experiments flag in Quest Browser and relaunch it. [Body tracking example and prerequisites](https://vuer-py.readthedocs.io/en/latest/examples/vr_xr/body_tracking.html). The schema includes the head, torso and hands through all five fingertips. A missing joint carries zero validity. WebXR may emulate occluded joints. A reference-space reset ends capture so the table can be registered again. ADB is available at `~/Library/Android/sdk/platform-tools/adb` on the development Mac.

With an independently measured headset registration, pass `--calibration /path/to/quest.json` to the bridge. Supply the same named `space` and a rigid 4×4 `table_from_local_floor` in metres. Without it, the frame is labelled `quest_local_floor`. The bridge estimates station clock offset from low-RTT clock probes and labels the XR timestamp as predicted display time. Watch `clock_rtt_ns` alongside sample age.

## Read the identical robot input

Run `python3 Capture/observe.py` on the capture Mac to see each sample's identity and age. `--frames 1 --timeout 10 --jsonl` prints one complete wire frame. An external application can bind the same port instead of the observer. UDP packets are local, self-contained UTF-8 JSON, less than 60,000 bytes. `subject` identifies the performer. `session` changes on producer restart, `seq` and `station_ns` are decimal strings, and `space` identifies the coordinate registration. Each bone row is:

```text
[name, parent_index, x_m, y_m, z_m, qx, qy, qz, qw, confidence]
```

All transforms are expressed in the named frame (+X right, +Y up, -Z forward). The first bone is the root with parent -1. The receiver converts handedness and metres to UE centimetres, then computes parent-local bone transforms. Missing/late frames must expire on the robot side. Frame identity is preserved across both copies; strict simulation/render alignment additionally requires a common evaluation tick. The physical camera, headset articulation, contact authority and overlay each need independent verification.

## External Maker Arm contact simulation

The station can run a local MuJoCo 4.0 process with both official Maker Arm URDFs and an independently simulated tabletop token. It samples the registered human streams at a common target time, updates palm and forearm collision proxies, and returns robot poses and contact locations to Unreal. The arm holds its rest position with a provisional compliant controller. The model's dynamics, hardware registration and collision response still require physical validation. [Maker Arm model assessment](../docs/maker-arm-model.md).

1. Open the populated `/Game/Maps/TheHumanTouch` map. Run this in the editor's Python console (one paste using Python mode):

   ```python
   import pathlib, sys, unreal
   root = pathlib.Path(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir()))
   sys.path.insert(0, str(root / "Tools"))
   import contact_view
   contact_view.export_layout(root / "Saved/Capture/station-layout.json", "table_v1")
   contact_view.start()
   ```

   Layout export reads the current base actor poses and tabletop height. The station's named coordinate frame uses metres, +X right, +Y up, -Z forward, with origin at the UE world origin. Use this same frame when registering each capture device. Re-export the layout after moving the bases/table.

2. Stop any command-line observer using port 17861. Start the simulator from the repository root:

   ```sh
   uv run --script Capture/contact_sim.py --layout Unreal/TheHumanTouch/Saved/Capture/station-layout.json
   ```

   The default publishes 60 ticks per second with four physics substeps. It selects the latest frame at or before the common target (45 ms behind the station clock). Each tick carries the frame IDs actually applied, 20 link frames including both grasp centers, the token pose, the human collision proxies and contact points. Unregistered poses are ignored. Proxies expire when their sample is over 120 ms old at the target time. After tracking loss or a jump, two distinct consistent samples are required to re-enable contact. Stop the simulator with Control-C.

3. The edit viewport displays robot link outlines, applied forearms, the token and contacts. **Simulate in Editor** moves the 18 Maker Arm mesh actors in the play world to the returned poses. Type `contact_view.status()` in the editor's Python console to inspect the last tick, its inputs, contacts and freshness. `contact_view.stop()` releases port 17863. All contact geometry in this view corresponds to the same authoritative tick. The live skeletal preview draws independently at the Live Link evaluation time.

**Verified:** A physical Quest 3S published a 67-joint subject through Mac Hub. Over one nine-second sample the receiver got 406 frames and all ten fingertips had valid transforms. Three independent editor frame evaluations matched the external receiver's sequence and timestamp. A separate two-performer **synthetic** rehearsal produced simultaneous contacts on opposite grippers. Unreal received both events with the identical source IDs and simulation tick, and all 18 play-world mesh positions matched the transmitted positions. The Mac browser subsequently published 65 poses from 65 live webcam frames and the project editor evaluated the last 23-bone frame. Simultaneous live acquisition, measured table registration and two-person physical reaches still need an on-site test.

**Mac browser webcam option.** To preview the person while granting camera access to a browser, run `uv run --script Capture/webcam.py --browser --model "$HOME/Library/Caches/TheHumanTouch/pose_landmarker_lite.task"` (the Terminal launcher caches this model). Open `http://localhost:17864/` on the **Mac** and select **Start webcam**. Allow camera access when prompted. The page reports the number of successfully published poses. Each JPEG is sent over loopback to the same external pose solver, processed in memory and discarded. One frame is in flight at a time. When available, browser camera capture time is mapped to station time with local clock probes. Otherwise the packet labels the video presentation timestamp. Stop the Terminal camera source before starting the browser one. The Quest headset uses its own page on port **17862**.

If Quest Browser reports **No XR hardware found**, verify the page is on the worn headset and that USB forwarding is active after reconnecting (`adb reverse tcp:17862 tcp:17862`). The page checks AR/VR runtime availability before starting, and offers a VR retry when a passthrough session cannot start. On hardware exposing WebXR hand inputs, the receiver prefers optical wrist and finger joint transforms, with per-hand joint counts in `optical_joints`. Otherwise it uses the body joint poses from the headset.

## Register both performers at the physical table

Survey the marked table positions in UE world centimetres against the actual station base transforms. Put the measurements in a local JSON file under `Unreal/TheHumanTouch/Saved/Capture/`. Include a versioned `space` matching the simulator layout. For `board_corners_ue_cm`, supply three XYZ arrays for the printed pattern's outer top-left, top-right and bottom-left corners, as seen with the page title upright. For `quest_targets`, supply at least three objects with a unique `name` and measured XYZ array `ue_cm`. Use widely separated points. Give a fourth independent point in `quest_check_target` with the same fields. Measure the existing [board's scale bar](../output/pdf/table-registration-letter.pdf) before use.

- **Webcam:** In the Mac browser camera page, open **Table calibration** and save at least eight unmirrored views of the printed board around the image, tilted in different directions and at different distances. Keep the webcam at the operating resolution. Fix the board at its surveyed position, then take two separate images with the board and camera stationary. Run the command below with the actual image paths. The output includes lens intrinsics, distortion and the rigid camera-to-table transform. Restart the webcam producer with `--calibration "$station_dir/camera.json"` and validate touches to known points at several heights. The board check measures camera registration; human depth accuracy needs its own reach test.
- **Quest:** Leave the current immersive session streaming and put the controllers down. Stop other listeners on UDP 17861 while collecting touches. Follow the terminal prompts with a second person at the Mac. On a successful independent check, the running bridge picks up the result without ending the XR session. A later session uses its own registration. Re-run after recenter or boundary changes.

```sh
station_dir="$PWD/Unreal/TheHumanTouch/Saved/Capture"
uv run --script Capture/register_table.py camera --survey "$station_dir/survey.json" \
    --lens-views /path/to/lens/*.png --station-view /path/to/fixed.png --check-view /path/to/check.png \
    --output "$station_dir/camera.json"
uv run --script Capture/register_table.py quest --survey "$station_dir/survey.json" \
    --output "$station_dir/quest-session-registration.json"
```
