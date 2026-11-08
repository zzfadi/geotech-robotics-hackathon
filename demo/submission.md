# The Human Touch: Hold My Beer

Ready-to-paste submission copy, updated November 8, 2026 at 14:50 PST. Copy the fields below through Links. The editorial evidence notes at the end are for the team.

## Project title

The Human Touch: Hold My Beer

## Team and track

Team 01 — The Human Touch · Creative track

FADI, Brian Frager, Denys Dubinin, Saleh Aldwais and Takeshi Okamura.

## One-line pitch

A playful robot handover and a human-in-the-loop simulation platform for rehearsing how robots work with people.

## Project description

“Hold my beer” makes a difficult robotics problem instantly understandable: grasp an everyday object, bring it to a person and let go at the right moment. The Human Touch uses that interaction to explore a bigger idea: robots that work with people need ways to practise with people, including inside parallel virtual environments.

We built a MakerMods Maker Arm control stack, an interactive Unreal environment using the official arm model, webcam and Quest motion capture, and parallel virtual-arm experiments driven by Dream Labs. Our prototype connects physical robot control, human motion and simulated rehearsal; each component’s demonstrated results are described separately below.

**What worked on the physical robot:** Our recorded demo shows a real Maker Arm grasping and raising a water bottle. A person takes the bottle, and the gripper opens to complete the handover. This is one supervised demonstration. Separately, the Unreal-to-hardware bridge demonstrated real arms following virtual-arm motion, including lift, return-to-idle and release. The team also demonstrated VR control of the arm. These results establish physical manipulation and interactive control; autonomous hand detection, grip-triggered release and repeated end-to-end reliability are still unverified.

**What we demonstrated in simulation:** Dream Labs drove virtual-arm grasp-and-lift experiments in Unreal, including a cup/can pickup and put-down sequence. The editor-world interaction uses a planned reach, Dream grasp/lift calls and kinematic object carry. Parallel cup experiments also used scripted assistance and approximate grip/contact models. The final standup reported a simulated bottle-handover sequence, with human-detection integration still pending. These are simulation results; they do not establish autonomous transfer of a learned skill to the physical arm. Webcam and Quest capture also brought real human motion into Unreal in separate tests.

**What we learned and what comes next:** Grasp physics and last-step perception integration were harder than the animation of the interaction. We replaced the attempted LiveKit vision approach with an existing camera-based recognizer, but the full perception-to-physical-handover loop remains unfinished. A separate two-arm hardware-control fault was resolved by running one USB-CAN adapter per service process. Next we will validate the integrated handover over repeated physical trials, measure handover timing and failures, and test whether rehearsal with live human motion improves the real interaction.

## Sponsor and technology usage

MakerMods supplied the Maker Arm hardware, SDK and robot description. Dream Labs supplied the API used for simulated arm experiments. Unreal Engine, Quest headsets, webcam capture and Live Link Hub support the interactive environment and human capture. A Jetson Orin Nano ran local vision and simulation tests. LiveKit was explored for voice/events and vision; it is not part of the final demonstrated handover. No Dream-controlled physical grasp or Jetson-hosted physical handover is claimed here.

## Links

Physical bottle-handover video: https://youtu.be/GK2B8TnZ3BI

VR control video: https://youtu.be/Nhui-EnJoLI

Repository: https://github.com/zzfadi/geotech-robotics-hackathon

Team dashboard: https://zzfadi.github.io/geotech-robotics-hackathon/

---

## Editorial evidence notes — do not paste into the form

- **VR control video:** https://youtu.be/Nhui-EnJoLI, supplied by the submission owner after upload at 14:53 PST. Added as a separate supporting link; this addition does not establish any new autonomy, safety or repeatability claim.
- **Video reviewed:** the 59-second unlisted upload titled “MakerMods Physical AI Hacks - Founders Inc SF - Team 01,” described as “1st pass at robot handoff to a human.” Sampled frames show the gripper at the bottle around 0:21–0:41, a raised bottle with the recipient reaching around 0:46, recipient grip at 0:51 and the open gripper beside the recipient-held bottle at 0:56. This supports the visible supervised sequence, not its control mode, perception trigger, contact sensing or success rate. Do not call it a human-to-robot can transfer: the visible sequence ends with the person taking a water bottle.
- **Latest standup:** [Slack notes, 14:39 PST](https://geotech-org.slack.com/archives/C0CFR1X1W2E/p1794177557353809). VR-to-physical motion and simulated handover are team-reported. The notes explicitly leave human-detection/GPU integration pending and drop LiveKit vision. “Ten successful tests” is omitted because the notes do not define the test goal and earlier ten-table results differ.
- **Physical bridge:** [Table 10 real-arm evidence](../docs/table10-real-arms.md#root-cause-found-one-usb-can-adapter-per-process-2026-11-08-144x) records both real arms completing the web UI flow after per-arm process isolation, within 2.6 degrees of Unreal while moving and with zero CAN errors. This is a separate control path/run from the uploaded bottle video.
- **Simulation assistance:** [Table 10 editor-world cup/can tests](../docs/table10-real-arms.md#vr-cup-handover-with-dream-editor-world) disclose kinematic carry. [Parallel cup iteration log](../docs/validation/claude-cup-iterations-2026-11-07.md) records assistance. Do not present these as unassisted physical grasps or validated contact dynamics.
- **Capture and edge results:** detailed sources remain in the [archived overnight evidence draft](submission-evidence-overnight-2026-11-08.md). Edge-policy benchmarks are simulation-only; the current copy avoids presenting their rates as physical reliability.
- **Open form logistics:** submission route, cutoff and required organizer-only fields remain unconfirmed. No deadline, station/contact field or external submission receipt is invented. This document is finished copy, not a claim that the form has been submitted.
- **Media distinction:** any museum/character films from the pitch are concept illustrations, not robot trial footage. The linked YouTube take is the physical evidence.
