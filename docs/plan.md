# Team plan: Hold My Beer (updated Sunday, Nov 8, 5:20 AM)

This is the working plan for Sunday, rebuilt from Brian's **Day 1 EOD sync** (Brian and Denys, posted in #geotech-robotics at 7:04 PM) and the repo as it stood at 8:10 PM. The history of how we got here, including every Saturday decision, is in [the Saturday Slack log](slack-log-2026-11-07.md). Where tonight's notes change an earlier decision, it is marked **Supersedes**.

All hackathon work, plans and handoffs live in this repo only; the product-factory repo is not used for it (FADI, 8:28 PM). Credentials stay out of this file. The Dream key is the `DREAM_API_KEY` environment variable from the team password manager.

## Sunday morning: proposed (Mac mini overnight, 04:30)

Drafted overnight on FADI's Mac mini while the arms were disconnected. **Every item in this section is PROPOSED for the 9 AM standup, not agreed.** It replaces no decision recorded below; the open questions further down stay open until the standup answers them.

1. **PROPOSED: name the e-stop person at the 8:30 hardware check.** Nothing on the arm is powered before that person is named and at the stop. The turn gate and the arm server's guards are software, not a safety stop ([handoff limits](../handoff/README.md#limits)).
2. **PROPOSED: a submission owner.** Default: whoever is not at the arm station. The 2-hour block is already in Sunday logistics below; the 12:30 to 14:30 slot is from Oracle's 8:50 PM draft in [the Saturday Slack log](slack-log-2026-11-07.md#proposed-sunday-run-of-day-oracle-850-pm-at-brians-request-not-yet-agreed).
3. **PROPOSED: the taped mark first.** The live beat is the arm picking the can up from a taped mark. At **11:00**, the go/no-go for hand mode: the hand-off replaces the mark only if it reaches **8 of 10** live trials; otherwise we demo the mark. `python3 -m handoff.trials` makes the count ([morning checklist](../handoff/README.md#what-must-be-tried-on-the-real-arm-sunday-morning)).
4. **PROPOSED: the fist bump is one fixed closed-gripper pose.** Teach a `fist` pose and play it by hand with `pickup_client.py joints`; the routine never uses it. Cut it at 11:00 if it isn't locked. It still has no owner.
5. **PROPOSED: the hold interlock and the Isaac → Unreal stream are out of the MVP** (rows 6 and 7 of the owners table). The operator on the e-stop and the arm server's own guards stay.
6. **PROPOSED: voice = scripted local lines** from [`voice_agent/`](../voice_agent/README.md), played on a local speaker. The LiveKit room goes on top only if Takeshi's 10-minute setup in [livekit-fit.md](livekit-fit.md#morning-setup-for-takeshi-about-10-minutes) works. The voice never moves the arm. The line text still needs Brian's or FADI's review.
7. **PROPOSED: Dream stays off the real arm.** That is the code default (`vision/hybrid.py`, `dream_on_real=False`) and a stated limit of the handoff routine ([handoff limits](../handoff/README.md#limits)). Confirm the Dream/Codex split at standup (Needs a decision, item 2).
8. **PROPOSED: the Jetson is not Sunday's arm host** unless the mark is locked and a read-only arm test passes on it (the "Read-only hardware" step in [the Jetson brief](handoffs/jetson-end-to-end.md): `scan` and `watch` both adapters; the exact command is still to be named). Until then the arm runs on the machine that holds the right arm's CAN adapter (today a laptop over USB, per the same brief).

### Run of day (PROPOSED)

Times follow Oracle's 8:50 PM draft in the Slack log, except that the mark trials end at the 11:00 call instead of building on to 11:30.

| Time | What |
| --- | --- |
| 08:30 | Arm station opens. Hardware and e-stop check; everyone pulls `main`; `python3 -m handoff.preflight --run-tests` on the station machine |
| 09:00 | Standup: settle the items above and Needs a decision |
| 09:15–11:00 | Teach the poses, then the taped-mark trials (the checklist aims to lock the mark by about 10:15); hand trials only if preflight says GO |
| 11:00 | The call: hand mode at 8 of 10, otherwise the mark. Fist bump in or cut |
| 11:30 | Feature freeze; commit the taught `poses.json` |
| 11:30–12:30 | Film the backup video |
| 12:30–14:30 | Submission |
| 15:00 | Demo on stage; everyone on site by 3 PM (already in Sunday logistics below, not a new proposal) |

### What landed overnight

Software, simulation and Jetson setup only. Nothing here moved the real arm, ran the hand-off from a person's hand, used LiveKit Cloud, measured a real Dream round trip or ran the arm from the Jetson. This covers the Mac mini work only; teammates' Saturday-night commits up to about 00:10 (Denys's simulated cup work, Brian's pitch and Quest work) are in `git log` and not summarized here.

- **Station readiness** (`b66503a`): a mark-first checklist in [handoff/README.md](../handoff/README.md), a hardware-free preflight ([handoff/preflight.py](../handoff/preflight.py), GO / MARK-ONLY / NO-GO), `pickup_server.py` claiming its port before it enables the motors, a hidden-hand fail-safe (the arm lets go only after the camera sees the hand off the can), and the control center moved to 127.0.0.1:8780. Software tests: 144 arm, 50 handoff, 20 of 20 replay scenarios, 17 control center ([update](../dashboard/updates/2026-11-08-0344-station-readiness-software.json)). Not run on the real arm.
- **Detector and Dream probe** (`132ee2e`): the real YOLO26n and YOLO26n-pose weights ran on still photos on the Mac mini (M4), about 48–53 ms per frame on the CPU and 22–26 ms on the Apple GPU; the hand boxes land on the wrist and need tuning ([validation](validation/vision-ultralytics-mac-2026-11-08.json)). Mac numbers; the Orin numbers and a live webcam run with nobody in view are in the Jetson hub check below. The Dream latency probe (`vision/dream_latency.py`) has run only with made-up timings, because the Dream key is not on the Mac ([hybrid-model.md](hybrid-model.md)).
- **Dashboard** (`47d7383`): a plainer overview, a How it works page and an updates page ([dashboard/README.md](../dashboard/README.md)); `025d537` refreshed How it works for the voice and the edge model.
- **Scripted voice and LiveKit bridge** (`bb9326f`): 31 scripted lines in two arm personas, played from a local speaker, plus a publish-only bridge into a LiveKit room ([voice_agent/](../voice_agent/README.md), [livekit-fit.md](livekit-fit.md)). Tested on a **local** LiveKit dev server only (36 of 36 messages arrived on the re-run). Not run on LiveKit Cloud and not heard on a stage speaker.
- **Edge model, simulation only** (`7ac2e28`): trained on our own scripted expert and scored on a kinematic bench, 180 held-out simulated attempts per row. Model alone 172 vs the expert's 167 (within noise); bridging late replies 173 vs 170 when the arm waits (within noise); a simulated operator aiming 2–4 cm off held the cup 162 times with assist vs 18 alone. In the MuJoCo physics sim, the expert held 18 of 18 and the model 17 of 18 with grasp-friendly contact settings, and the expert 0 of 18 with MuJoCo's defaults ([validation](validation/edge-model-bench-2026-11-08.json), [edge_model/README.md](../edge_model/README.md)). Later run in simulation on the Jetson's CPU (the hub check below); never on a real arm.
- **Jetson Orin Nano installed** (`4313e49`, `24d61e1`): Ubuntu 24.04.4 and L4T 39.2.1 on the NVMe, key-based SSH, an isolated Python 3.12.15 runtime that passes 140 offline arm tests in 2.77 s without touching hardware, and still capture from the C270 webcam on the Jetson ([orin-nano-setup.md](handoffs/orin-nano-setup.md)). No CAN adapter, controller or arm session on it yet.
- **Jetson hub check done, no arms or CAN attached** (`0be16ee`, `19c5ae4`; fixes for what it found in `0306c8a`): offline suites pass, TensorRT FP16 engines take 26–41 ms per frame for detect + pose, a live C270 loop ran at the camera's 15 fps with nobody in view, and the edge model takes 0.038 ms per decision on the Jetson's CPU ([jetson-hub-testing.md](jetson-hub-testing.md)). Hands and the can on a live camera are untested.

## Needs a decision

Nobody owns these yet, and nothing below assigns them. Settle them first thing Sunday at the latest.

**Top two**

1. Who owns the beer handoff on the real arm: take a can from a person's hand, then the fist bump. **Answered:** FADI owns the handoff (voice call, about 10 PM Saturday). The routine and the morning checklist are in [handoff/](../handoff/README.md); the fist bump is still unowned.
2. How Dream-1 and Codex split the real arm: which moves Dream-1 drives and which Codex scripts directly.
   - Default in code, not yet confirmed: the real arm runs local (`vision/hybrid.py`, `dream_on_real=False`) and the handoff routine has no Dream path ([handoff/README.md limits](../handoff/README.md#limits)); FADI's 10 PM quote in [docs/hybrid-model.md](hybrid-model.md) sends the full movement to Dream when the network is good, so confirm at standup.

**Also no owner yet**

- The hold interlock: Isaac → LiveKit "hold" message into our arm controller, dry-run tested.
- The Isaac → Unreal stream over LiveKit.
- The LiveKit voice agent.
- Submission: block about 2 hours and run it.
- The Luna2 simulated hand-to-arm contact trial.

**Other open questions**

- Is the voice agent still a must-have? The EOD notes don't mention it.
- Is the hold interlock (and the turn gate) in the MVP?
- When exactly is submission due, given "on site by 3 PM" and polishing time after the deadline? Who owns it?
- Ten parallel simulations or nine? Nine run today. For the record, [docs/maker-arm-fleet.md](maker-arm-fleet.md) line 29 records a ten-table simulated batch on Nov 7 (`c4550219…`: all ten ran together for 170 s; line 31 adds that seven moved and none passed the 10 cm held lift), and its line 21 calls the ten-table results historical beside the current nine tables. That does not decide the number for the pitch.
- **Flag, not resolved:** [README.md](../README.md) (line 7) and [docs/hold-my-beer.md](hold-my-beer.md) define Hold My Beer as a **simulated** cup lift (a Maker Arm grasps a drinking cup, lifts it 10 cm and holds it steady), while this plan defines it as the **physical** arm taking a can. Which meaning the pitch and submission use is open.
- Should Luna2's Isaac scene-parity scripts be committed to the repo?

## The goal

Brian holds out a beer, a physical Maker Arm reaches out and takes it, then fist-bumps him. Then the pitch: human-robot interaction needs real humans, not pure simulation, shown with the parallel Unreal tables and webcam and VR capture. **MVP only, nothing extra.**

## What changed tonight

| Topic | Earlier plan (Saturday afternoon) | Now (EOD sync) |
| --- | --- | --- |
| Who drives the physical arm | Dream-1 as the default "brain" for the real run (Brian, 4:40 PM), voice order → LiveKit agent → Dream-1 | **Supersedes:** a coding agent (Codex) drives the arm directly through the Maker Arm SDK. The EOD notes skipped Dream Labs on the real arm for now, but FADI confirmed at 8:31 PM that **we keep using the Dream API** alongside Codex, not Codex alone. How the two split the real arm is open (see Needs a decision). Long term, maybe open-weights arm control. |
| Where Dream-1 runs | Real arm, with optional sim rehearsal | Simulated arms in Unreal: virtual cameras send frames to the Dream API, Dream returns positions, the SDK moves the virtual arms (Denys). |
| Demo story | "Voice order → packed tray", then a cube push in Isaac | **Supersedes:** "Hold my beer" gag, fist bump, then the parallel-sim and capture story. |
| What the simulation is for | Rehearsal and visuals | Collecting real human interaction data and benchmarking models against simulated humans. Visuals are secondary. |
| Pause when someone is in the way | Isaac's "hold" message pauses Dream-1 through the portal task monitor | Still the design, but the hold has to stop whichever drives the arm, Codex or Dream-1, so it goes into our own SDK controller. Not mentioned in the EOD notes, so it is an open question whether it is in the MVP. |

Unchanged from Saturday: Isaac does the physics and streams results into Unreal over LiveKit; Unreal only draws what Isaac owns; the hold is a **software interlock, not a safety stop**, and the station operator stays on the e-stop; freeze the table layout before rehearsals; if the stream drops, Unreal holds the last pose and shows "sim offline". Details are in the Slack log under "Decisions Brian confirmed at 4:54 PM".

## Build status by person (as of Saturday night)

| Who | Area | Status |
| --- | --- | --- |
| FADI | Physical arm, direct SDK control | Right arm picked up the yellow cylinder: guarded holding controller plus IK client, grasp, ~6 cm lift, place and return to rest in 16 s (`dde9844`, [arm/README.md](../arm/README.md)). Left arm attempt missed high. No hand-to-arm beer handoff yet. |
| Denys | Unreal simulation + Dream API | Nine parallel tables running, Dream API driving the virtual arms. Cup scene and contact work ongoing in the repo (latest `9d83c39`). Polishing the parallel demo; has the Quest 2 overnight for XR. |
| Brian | XR, body tracking, performance capture | Webcam or VR capture streams a person into Unreal; Isaac handles physics and sends results back; Quest passthrough overlays the virtual table on the real one. Working on the pitch creative tonight and Sunday morning. |
| Takeshi | Cameras | Camera calibration and an Ouster camera mounted on the arm. Earlier also named for LiveKit and UI. |
| Saleh | Arm hardware | Earlier hardware integration problems resolved with a different approach. |

## Demo script outline (3 minutes)

Brian owns the script; this is the outline from the EOD notes for him to fill in.

1. **Cold open, "hold my beer":** Brian holds out a beer (empty or foam can, no glass or liquid), the arm reaches out and grabs it.
2. **Fist bump:** the arm fist-bumps Brian after taking it (if it's reliable; cut first).
3. **The why:** human-robot interaction needs real humans, not pure simulation.
4. **Scale:** all the Unreal simulations running in parallel (notes say 10; nine run today).
5. **Inputs:** webcam and VR capture shown side by side as two ways to bring a person in.
6. **What the arm is thinking:** Denys's emoji overlay ("seeing beer") on screen, which replaces a slide.
7. **Close:** what failed and what's next (the rubric rewards saying this).

If we reach the finals, a **5-minute version** goes deeper. Brian keeps the Quest 3 for the presentation.

Idea flagged, not committed: **assisted intent**, where the arm infers what the person means and smooths the motion instead of copying the hand (like a phone tap snapping to the nearest button). Dream fine-tuning might help the last-moment precision.

## Sunday logistics

- Brian works from home in the morning and arrives mid-morning.
- **Everyone on site by 3 PM.** Demos are judged on stage at 3 PM.
- **Block about 2 hours for submission**, because submission always has problems. Name an owner for it.
- The notes say up to ~3 hours of polishing remain after the submission deadline. Confirm the real cutoff with the organizers.
- MVP only.

## Owners and next steps

| # | What | Owner | When |
| --- | --- | --- | --- |
| 1 | Pitch creative and "hold my beer" demo script | Brian | Tonight and Sunday morning |
| 2 | Smooth parallel-simulation demo, the visual proof of parallel human-robot training | Denys | Sunday morning |
| 3 | Quest 2 in debug mode, XR work overnight | Denys | Tonight |
| 4 | Emoji "thinking" overlay | Denys | Sunday |
| 5 | Physical arm takes a handed-over can from a person, then fist bump. Software ready: the routine, a mark-first checklist ([handoff/README.md](../handoff/README.md)) and a hardware-free preflight ([handoff/preflight.py](../handoff/preflight.py)); replay-tested only, **not yet run on the real arm** | FADI for the handoff; fist bump **no owner named** | Handoff 8 of 10 by 11:00, else the taped mark |
| 6 | Hold interlock: Isaac → LiveKit hold message into our SDK controller, dry-run tested | **No owner named** (earlier: Saleh and FADI) | Only if kept in the MVP |
| 7 | Isaac → Unreal stream over LiveKit | **No owner named** | Unclear if needed for the MVP |
| 8 | LiveKit voice agent (Brian called it a must-have on Saturday). Built overnight: scripted local lines ([voice_agent/](../voice_agent/README.md)) and a publish-only LiveKit bridge ([livekit-fit.md](livekit-fit.md)), tested on a local LiveKit dev server only | **Owner still to confirm** (earlier: Takeshi). The 10-minute LiveKit room setup in [livekit-fit.md](livekit-fit.md#morning-setup-for-takeshi-about-10-minutes) is written for Takeshi, who has the LiveKit project; not yet agreed with him | Unclear if still in; proposed: scripted lines, LiveKit only if the setup works |
| 9 | Run the Luna2 simulated hand-to-arm contact trial | **No owner named** | Sunday |
| 10 | Reserve 2 hours for submission and run it | **No owner named** | Before the cutoff |
| 11 | Jetson robot server, end to end ([brief](handoffs/jetson-end-to-end.md)). OS, SSH and 140 offline arm tests verified ([setup](handoffs/orin-nano-setup.md)); Jetson hub check done, no arms or CAN attached (`0be16ee`, `19c5ae4`): offline suites pass, FP16 engines 26–41 ms per frame, edge model 0.038 ms per decision ([jetson-hub-testing.md](jetson-hub-testing.md)) | FADI, working on the Mac Mini from this repo | Overnight; not required for the MVP. Proposed: not Sunday's arm host unless the mark is locked and a read-only arm test passes on it |
| 12 | Train and score a small edge model for the Orin Nano: Dream lag bridging and auto-assist, no Dream fine-tune ([brief](handoffs/edge-model.md)). Trained and scored in simulation only ([validation](validation/edge-model-bench-2026-11-08.json)); also run in simulation on the Jetson's CPU, 0.038 ms per decision ([validation](validation/edge-model-jetson-2026-11-08.json)); never on a real arm | FADI, working on the Mac Mini from this repo | Time-boxed overnight; done in simulation only; not required for the MVP |

## Hybrid model: local detector plus Dream (added Nov 7, 10:30 PM)

FADI's plan from tonight's call, in full in [hybrid-model.md](hybrid-model.md), with a first build in [`vision/`](../vision/README.md):

- A loop on the Orin Nano runs a local YOLO detector (YOLO26n plus its pose model, converted to TensorRT) for moment-to-moment detection: hand entered, can offered, can held steady, can released, hand withdrawn.
- A steady offered can is what requests the arm's turn; an extra hand, a jerked can or a detector failure revokes it through the existing turn gate.
- When Dream's median round trip is at or under 850 ms, Dream does the full movement (sim). When it's slower, or on the real arm, the local controller does it. The last centimetre near the can is always local: the same code serves the handoff, aim assist and lag bridging.
- FADI owns the can handoff on the real arm.
- For the 9 AM standup: agree that a steady offered can sends `request_turn`, and test the detector on the real camera and can.

## Handoffs for the Mac Mini

Three briefs cover the work that needs the Mac or hardware: the two workstreams FADI owns (the Jetson and the edge model), which he works on the Mac Mini from this repo with whichever agents run there, plus the Dream workshop notes that feed both. The briefs are written so a fresh agent can start cold. Since Nov 8 the Mac mini is the team's development hub (FADI, [dashboard update, 02:33](../dashboard/updates/2026-11-08-0233-mac-mini-dev-hub-next-three.json)).

- [Jetson robot server, end to end](handoffs/jetson-end-to-end.md)
- [Train a small model from our simulation](handoffs/small-model-training.md), now aimed at [the edge model](handoffs/edge-model.md)
- [Dream workshop takeaways](handoffs/dream-workshop-takeaways.md)

Also read before working from the Mac mini:

- [Can handoff on the real arm](../handoff/README.md): the routine, the mark-first checklist and the preflight
- [Hybrid model: local detector plus Dream](hybrid-model.md)
- [Jetson Orin Nano setup](handoffs/orin-nano-setup.md)
- [LiveKit fit for Hold My Beer](livekit-fit.md)

The MVP arm still runs on the machine that holds the right arm's CAN adapter (today a laptop over USB, per [the Jetson brief](handoffs/jetson-end-to-end.md)), not on the Mac mini: `pickup_server.py` listens on 127.0.0.1 only and `handoff.run` connects only to localhost.

