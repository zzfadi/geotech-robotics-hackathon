# Quest 3 look-to-relock rehearsal

Probe the station once. During a take, the wearer can look closely at the left base, shift their head a few centimetres, then do the same on the right. The Mac operator sees either a correction or a reason the overlay is holding. Each wearer has their own local alignment in the Luna2 session. Use the static tabletop dock below as a second way to start.

## Make the actual holes visible

1. Park and disable both arms. Face the station from the arm-side long edge. On each base identify the six 3.5 mm upper-face bores in two columns, 180 mm apart, with rows at 0, 40 and 80 mm. Use calipers to verify the pattern. The long dimension runs left to right from this viewpoint.
2. Color only the flat rim around each factory hole. Use matte magenta at the **near-left corner of each plate**, matte cyan at the other five left-base holes, and matte yellow at the other five right-base holes. Use removable vinyl or paint on a removable film. Center the 10 mm outer rings on the actual apertures and open the centers fully. The physical dark holes provide the measured points. A filled dot, a screw hiding the bore, a glossy reflection or a shifted sticker can all prevent registration. Leave the mounting hardware installed. [Actual-size ring sheet and placement diagram](../output/pdf/quest-relock-color-rims.pdf)
3. Print at **100% / Actual Size** and check the 100 mm bar with a ruler. Check the colors with the actual camera under demo lighting. Start about 30–45 cm away, looking down at one complete plate. The camera needs a distinct dark opening on all six rings, at least 4 pixels across the short axis and at least 25/255 brighter on the rim. The operator command below must print a PASS for **each** base. If it holds, approach more squarely, remove glare, improve diffuse lighting or replace the rim. A covered or unresolvable bore disqualifies optical re-lock.

## Start the camera and qualify it once

Use the local Mac running the streaming Unreal editor. Keep the same Quest connected to OXRSys and authorized in ADB. A USB data cable is the simplest camera transport even when joining Luna2 over the network. Paired wireless ADB also works if transfer and tracking meet the timing checks. The operator commands run from this repository root.

```sh
adb devices -l
adb -s SERIAL install -r Unreal/TheHumanTouch/Saved/Rehearsal/OXRSys-quest3-look-to-relock.apk
ht-rejoin --passthrough --table 10
adb -s SERIAL shell am start -n net.demonixis.oxrsys.android/com.oculus.NativeActivity --ez oxrsys_relock true
uv run --script Capture/quest_relock.py contrast --serial SERIAL
```

Replace SERIAL with the attached Quest's ADB identifier and 10 with the physical station's scene number. Accept headset camera access in OXRSys. The APK is prepared locally on the rehearsal Mac. On another machine build the Android release from the [OXRSys Quest source and instructions](https://github.com/Geodesic-Games/OpenXR-OSX/blob/main/docs/platforms/quest.md#opt-in-quest-station-camera). The Mac may need its local SDK's `platform-tools/adb` on PATH.

1. Complete the [mechanical station survey](quest-demo-registration-and-capture.md#register-against-the-hardware) in the local Unreal editor, including the two independent repeats. It checks the tabletop, parked tools and both bases. For a later startup with a qualified lens and cradle, use the dock instead.
2. In Unreal's console enter `HumanTouch.Relock on`. Run the following on the Mac. Hold still briefly at each viewpoint. Move between views, vary distance and angle, and put the complete plate in different parts of the camera image. Collect at least eight accepted views of each plate. Varying just head yaw from one fixed point is insufficient.

   ```sh
   uv run --script Capture/quest_relock.py enroll --serial SERIAL
   ```

   Wait for **Qualified camera profile**. The solver estimates the lens intrinsics and camera-to-head transform for that headset, crop and OS build, then checks held-out views from both plates. A failed attempt preserves the previous profile. Reuse the accepted profile for subsequent starts with this headset and camera mode. A new headset, OS camera mode or changed physical lens requires enrollment again.
3. First run the watcher in observation mode. Briefly hold still, then shift sideways at least 2.5 cm and hold again. Either sight the plates separately or capture them together when all twelve holes pass the resolution and contrast checks. Do this on both plates within 12 seconds. The operator should see an observed correction or “already aligned within 2 mm.” During rehearsal, cover the magenta key and check the worker reports HOLD and the room stays still. Uncover and reacquire both plates. Verify the real and virtual corners and both parked tool centers from a second angle before enabling automatic correction.

   ```sh
   uv run --script Capture/quest_relock.py watch --serial SERIAL
   # After the observation check, stop with Ctrl-C and start the correction worker:
   uv run --script Capture/quest_relock.py watch --serial SERIAL --apply --seconds 3600
   ```

A correction needs three fresh sightings per base, agreement of the independent fits at both bases within 6 mm and 0.65°, and a passing withheld hole check. The final change is limited to 4 cm at either reference and 2.5° yaw. Tracking provides motion between sightings. Ambiguity, glare, occlusion, a stale frame or a large discrepancy leaves the last alignment in place. A recenter, tracking-space change or moved station requires a new seed. The update changes only the local XR room translation and yaw at unit scale. An incorrect mounting height, tabletop size or robot joint mapping needs a physical/model correction. For the known saved station dimensions, see the mechanical survey guide above.

## Static midpoint dock

Mark the centers of the two installed bases from their hole patterns. Mark the point **halfway between those centers in plan**, on the **tabletop surface**. Give the headset a stable resting support with an indexed heading, e.g. a shallow cradle against two fixed stops. Face its front straight across the table, away from the operator. Keep cameras unobstructed, the strap clear of the support and tracking awake while unworn. If needed, use MVDH Device Manager's proximity-sensor setting and restore it afterward. [Meta device controls](https://developers.meta.com/vr/documentation/spatial-sdk/ts-mqdh-media/)

- **Teach the cradle once.** After a successful probe, set the live headset in the cradle and enter `HumanTouch.Dock learn Q3_demo` in Unreal. Wait for **Saved this local headset/cradle profile**. Use a distinct name for each headset and cradle. This measures the offset from the physical tabletop datum to the device's tracked head center, including heading and resting tilt.
- **Initialize from the table.** With the same headset and cradle in exactly that position, enter `HumanTouch.Dock snap Q3_demo`. There is a three-second delay and a stability check of at least 1.25 seconds. Wait for **Static midpoint alignment applied** before lifting the headset. The operator then wears it and looks at the bases to verify the optical fit. Dock-only registration is marked provisional until independently checked against the real station.
- **First use with an independently measured fixture.** If the cradle has never been taught, use `HumanTouch.Dock offset FORWARD_CM RIGHT_CM UP_CM YAW_DEG`. These are the measured offsets of the **tracked head center** from the marked midpoint in the docked pose, with positive forward across the table, right toward the operator's right, and up from the tabletop. Positive yaw turns toward the right. The headset shell, camera lens and tracked origin are different points. If the offset is unknown, use the survey to teach it. A guessed eye height defeats the fixed-position fallback.

Stop the watcher with Ctrl-C. `HumanTouch.Relock off` disables local optical corrections. The same Android launch command with `--ez oxrsys_relock false` closes camera access. `HumanTouch.Dock cancel` cancels a pending placement. Camera images are processed in memory. Local calibration and status files live under `Unreal/TheHumanTouch/Saved/Capture/Relock/`.

## Rehearsal acceptance

Run the contrast check in the actual lighting, verify three nonadjacent physical references including a far corner and both arms, cover and uncover a key, then repeat the dock placement from a fresh start and check the overlay from both sides. Record the measured residuals. The automated tests use simulated images and known poses. Hardware contrast, tracking jitter, room overlay accuracy and recovery after a real drift event require this bench check. [Cast and record the composite for the pitch](quest-demo-registration-and-capture.md#share-the-mixed-reality-view-live)
