# The Human Touch: Hold My Beer

Ready-to-paste submission copy, revised November 8, 2026. Copy the fields below through Links. The editorial evidence notes at the end are for the team.

## Project title

The Human Touch: Hold My Beer

## Team and track

Team 01 — The Human Touch · Creative track

FADI, Brian Frager, Denys Dubinin, Saleh Aldwais and Takeshi Okamura.

## One-line pitch

Teaching robots when and how to lend a hand

## What did you build?

When someone starts pouring from a heavy pitcher, a helpful partner steadies the bowl. That choice is the problem we want robots to learn. As robots enter homes, workplaces and care settings, whose idea of help will they follow? How close to stand, when to reach and whether to wait can vary across cultures, across abilities and even for the same person from one moment to the next.

We built The Human Touch, a virtual rehearsal space where people can help teach robots how to work alongside them. A headset or webcam streams a person’s movement into Unreal Engine. Nine virtual tables let us rehearse variations in parallel using models of the MakerMods arms. A shared virtual lab makes it possible for people around the world to contribute without owning a robot or traveling to a station.

Our goal is a dataset people shape themselves. With their consent, we want to record what the person did, how the robot responded and what they would have preferred. A hesitation, a change of hands or a request to stop gives developers a case they can replay. Those examples could train robot policies through reinforcement learning and let us test whether people with different expectations actually find the learned behavior helpful.

## Progress during the hackathon

We started with a water bottle because anyone can judge the moment a handover works. In our filmed hardware trial, a MakerMods arm grasped the bottle, lifted it to a person and opened its gripper as they took it. In a separate test, the physical arms followed movements from Unreal through the MakerMods SDK. We also demonstrated VR control.

In Unreal we used the official MakerMods URDF to model the arms. We streamed a performer’s body pose from a webcam and head and hand motion from a Quest headset in real time. Across the virtual tables, Dream-1 drove grasp and lift actions with a scripted reach and grip assistance. We could repeat the task and inspect where a grasp failed. We also ran camera-based person, hand and bottle detection on a Jetson Orin Nano.

Our next experiment keeps the bottle and varies the person’s response. Participants can accept it, switch hands or pull away. We’ll record which robot response each participant preferred, then test with new participants whether a policy trained on those examples offers, waits or withdraws at the right moment.

## Sponsor and technology usage

MakerMods supplied the Maker Arm hardware, SDK and robot description. Dream Labs supplied the API used for simulated arm experiments. Unreal Engine, Quest headsets, webcam capture and Live Link Hub support the interactive environment and human capture. A Jetson Orin Nano ran live person, hand-pose and bottle detection on a real webcam at about 30–37 ms per frame (TensorRT FP16), alongside local simulation tests; it is a watch-only view and does not drive the arms. LiveKit was explored for voice/events and vision; it is not part of the final demonstrated handover. No Dream-controlled physical grasp or Jetson-hosted physical handover is claimed here.

## Links

Team image: https://zzfadi.github.io/geotech-robotics-hackathon/assets/the-human-touch-team-logo.png

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
