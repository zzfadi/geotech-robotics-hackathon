# Quest 3 tabletop calibration

The default demo uses one stationary alignment. During the take the Quest tracks the wearer's movement and Unreal keeps the calibrated room transform fixed. The optional camera mode can correct small drift using the **six flush silver screw heads on each black base**, without adding markers.

## Start the short demo

1. On the XR Mac, run `ht-rejoin --passthrough --table 10` (use the number for your station). Open OXRSys and wait for the live scene. Default launches need no camera enrollment, camera permission or optical worker.
2. Use the **long edge opposite the robot arms**. Its midpoint is **48 inches from either end**. Rest the headset directly on the tabletop at that midpoint, with the **back edge of the rigid headset housing flush with the table edge**, front cameras facing straight toward the arms. Keep the strap clear so it cannot tilt the housing. Keep floor-level tracking awake while the headset is unworn.
3. In **Tools → Capture Performers**, click **Calibrate from headset on table**, or enter **`HumanTouch.Dock edge`** in the local Unreal console. Leave it still through the three-second delay and at least 1.25 seconds of stable tracking. Wait for the confirmation in the Output Log, then lift and wear it. Check both bases and a far tabletop corner before the take.

The local XR tabletop and the mechanical survey both use **243.84 × 71.12 cm** (96 × 28 inches). The top stays centered at the scene's existing height, and both robot meshes keep their CAD scale and positions. The authored Table 10 flange top is 15.7 mm below the virtual tabletop. Check that mounting height and both base positions on the physical bench. A room transform cannot repair an incorrect relative mounting height.

**First use.** With no taught edge profile, `HumanTouch.Dock edge` uses the tracked floor height and puts the head's horizontal tracking origin at the edge midpoint as a provisional starting position. The back of the shell and the tracking origin can be several centimetres apart. For a repeatable calibrated placement, complete the [mechanical probe survey](quest-demo-registration-and-capture.md#register-against-the-hardware) once, then put the headset at the same physical edge and run `HumanTouch.Dock edge learn quest3`. Subsequent `HumanTouch.Dock edge` commands use that measured shell offset and check resting tilt. For another headset or facial interface use a separate name with `edge learn NAME` and `edge snap NAME`.

If you have measured the shell-to-tracking-center transform independently, use `HumanTouch.Dock edge offset FORWARD_CM RIGHT_CM UP_CM YAW_DEG`. Forward is inward from the edge toward the arms, right is the wearer's right facing the arms, and height is measured up from the tabletop. The previous cradle between the arm bases remains available as `HumanTouch.Dock midpoint snap NAME`. Its profiles are separate.

## Optional silver screw drift correction

Start from a successful probe or a stationary dock. The flush screw faces have **0 mm offset** from the black flange. Use the two rows of three small screws on each base. A bright reflection inside one screw or a different fastener on the arm is insufficient. Use diffuse lighting, avoid blown highlights and look at the whole plate. The prior alignment predicts which base and which way round the symmetric pattern should be.

**First optical setup on each headset.** Install the rehearsal OXRSys APK on the authorized Quest. On the configured Mac the APK is under `Unreal/TheHumanTouch/Saved/Rehearsal/`. The existing station-camera APK works with this release. Run the commands below from the project root, substituting the actual ADB device identifier. For a headset with no current camera permission, grant access in OXRSys. [OXRSys Camera2 build and protocol](https://github.com/Geodesic-Games/OpenXR-OSX/blob/main/docs/platforms/quest.md#opt-in-quest-station-camera)

```sh
adb devices -l
adb -s SERIAL install -r Unreal/TheHumanTouch/Saved/Rehearsal/OXRSys-quest3-look-to-relock.apk
adb -s SERIAL shell am start -n net.demonixis.oxrsys.android/com.oculus.NativeActivity --ez oxrsys_relock true
```

After the probe or dock, enter **`HumanTouch.Relock on`** to publish head poses for observation only. Run these in order:

```sh
uv run --script Capture/quest_relock.py contrast --serial SERIAL
uv run --script Capture/quest_relock.py enroll --serial SERIAL
uv run --script Capture/quest_relock.py watch --serial SERIAL
```

Look at each base in turn for the contrast check. Both must pass. During enrollment move between several distances and oblique angles, placing the complete pattern at different points in the image. Hold still briefly at each of at least eight accepted viewpoints per base. Wait for **Qualified camera profile**. With a dock seed, enrollment estimates any small room offset separately from the lens calibration and checks held-out views. Enrollment saves the local lens profile and leaves the room in place. A previously qualified lens profile for the identical headset, OS and camera mode can be reused.

**To opt in on a later launch**, use `ht-rejoin --passthrough --table 10 --drift-correction`. The project launcher accepts the same flag. It enables writes for that editor and starts a watcher for its PID, with output under `Unreal/TheHumanTouch/Saved/CoPresenceLogs/quest-drift.log`. Check the observation-only output before a live take. The watcher needs one authorized headset or an explicit `--quest-serial SERIAL` on the project launcher. On first use, enroll the lens as described above. To enable corrections manually in a running editor, enter `HumanTouch.Relock apply` and run `uv run --script Capture/quest_relock.py watch --serial SERIAL --drift-correction`. `HumanTouch.Relock off` immediately disables writes. Ctrl-C stops a foreground watcher. Relaunch without the flag for initial calibration only.

**Acceptance in the actual room.** Briefly hold on each base from two positions at least 2.5 cm apart, with both bases seen within 12 seconds. The worker needs three fresh exposures per base, independent fits agreeing within 6 mm and 0.65°, and a passing withheld-screw check. Ordinary corrections are limited to 4 cm and 2.5° at unit scale. Ambiguous patterns, movement during exposure or disagreement hold the previous alignment. If the starting pose is outside the correction range, use a taught or measured dock, or re-probe. Verify the two real bases within 5 mm and tabletop corners within 10 mm. Cover screws or introduce glare and confirm a hold, then reacquire. Color-free detection has been tested on simulated camera images; hardware acceptance requires this check in demo lighting. [Cast or record the Quest composite](quest-demo-registration-and-capture.md#share-the-mixed-reality-view-live)
