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

The solver produces a 23-bone torso, head, arms, hand endpoints and legs. Joint confidence is available as Live Link curves named `<bone>_confidence`. Keep the person in view, including hips; crop to the second performer's part of the frame with `--roi x0 y0 x1 y1` in normalized image coordinates. The source skips frames when it detects zero or multiple people inside that region. Rehearse crossings; the selected ROI itself does not identify a particular person. A still-image smoke test uses `uv run --script Capture/webcam.py --model /path/to/model.task --image /path/to/person.jpg --frames 60`.

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
