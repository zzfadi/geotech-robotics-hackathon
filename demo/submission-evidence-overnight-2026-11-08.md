> Historical overnight draft, archived at 14:50 PST on November 8. Its placeholders and pending-status statements are superseded by [submission.md](submission.md). Retained for the detailed benchmark evidence and provenance.

# Submission draft: The Human Touch · Hold My Beer

**Draft, written Sunday Nov 8 at about 04:40 PST and updated at about 05:20 with the Jetson results, before any Sunday arm trial.** For the submission owner: [SUBMISSION OWNER, named at the 9 AM standup]. You fill in three things: the footage, the 11:00 mode, and the organizers' form fields. Everything in [square brackets] is yours to fill in or delete. Every number below links to a file or commit. If a number isn't in the results table, don't add it to the form. The Sunday times in this draft (the 11:00 call, the 11:30 freeze, the filming and submission slots) are proposed in [docs/plan.md](../docs/plan.md), not agreed; confirm them at the 9 AM standup.

## 1. Fill these in first

| Field | Value |
| --- | --- |
| Demo video URL | [VIDEO URL] |
| Backup take (proposed slot 11:30 to 12:30) | [BACKUP VIDEO URL, or delete]. If the judges see it instead of a live run, say it is recorded |
| The 11:00 call | [HAND or TAPED MARK] |
| Live trial count behind the call | [X of 10 taped-mark trials; Y of 10 hand trials, or "hand not tried"], from `python3 -m handoff.trials` |
| Was one hand run done with Brian himself? | [YES / NO]. Only YES allows the words "from Brian's hand" |
| Fist bump in the demo? | [IN / CUT at 11:00] |
| Voice in the demo? | [Scripted local lines / none] |
| LiveKit Cloud setup worked? | [YES / NO / not tried]. Write "not tried" unless someone saw the bridge join a Cloud room on Sunday; see the claims checklist |
| Team name on the form | [TEAM NAME] |
| Track | [Creative track, per `dashboard/data/project.json`; confirm on the form] |
| Event name | [EVENT NAME as the organizers write it] |
| Station or table number | [STATION NUMBER] |
| Contact email(s) | [EMAIL] |
| Submission deadline | [CUTOFF; the organizers haven't published it in our sources. About 2 hours are reserved (agreed); the 12:30 to 14:30 slot is proposed] |
| Extra hardware we brought | [Confirm: Quest 2, Quest 3S, Logitech C270 webcam, Jetson Orin Nano, Mac mini, laptops]. The rubric permits a discount for equipment other teams lacked, so list it plainly |
| Other organizer fields | [FIELD]: [VALUE] |

Two rules from the rubric ([creative-rubric.md](pitch/deck/creative-rubric.md), [rubric update](../dashboard/updates/2026-11-07-1353-rubric-live-demo.json)): a video-only demo caps Demo quality at 3 of 5, and saying what failed and what is next is part of the Presentation score. Section 6 covers the second.

## 2. Title and one-line pitch

**The Human Touch: Hold My Beer**

Robots that work with people have to practise with real people, so we are building a way to bring real people into many simulated robot tables at once, and we show a real robot arm that picks up the can when you say "hold my beer".

Naming note: [README.md](../README.md) and [docs/hold-my-beer.md](../docs/hold-my-beer.md) use "Hold My Beer" for the simulated cup lift, and [docs/plan.md](../docs/plan.md) uses it for the real arm taking a can. The plan flags this as open. This draft uses the name for both; [confirm at standup].

## 3. Description (pick one variant, delete the other)

**Variant A: taped mark (the proposed default).** Use this unless hand mode reached 8 of 10 at 11:00, and only if the taped-mark trials passed. If neither passed, describe Saturday's recorded cylinder pickup instead and say it is recorded.

> Someone says "hold my beer", and a MakerMods Maker Arm picks an empty can up from a taped mark on the table. Our own guarded controller drives it, and an operator stays on the e-stop. Behind the gag, we built the pieces for robots to rehearse with real people: webcam and Quest headset capture that put a person's motion into Unreal, and a grid of simulated robot tables there where Dream's AI drives the virtual arms. So far these pieces have run separately, not as one rehearsal.

**Variant B: hand-off.** Use only if hand mode reached 8 of 10 live trials at 11:00. Write "from Brian's hand" only if a hand run was also done with Brian. Otherwise write "from a person's hand".

> [Brian / Someone] says "hold my beer" and holds out an empty can, and a MakerMods Maker Arm takes it [from Brian's hand / from their hand]. A local hand-and-can detector decides when the arm may move and when to close, our own guarded controller does the moving, and an operator stays on the e-stop. Behind the gag, we built the pieces for robots to rehearse with real people: webcam and Quest headset capture that put a person's motion into Unreal, and a grid of simulated robot tables there where Dream's AI drives the virtual arms. So far these pieces have run separately, not as one rehearsal.

## 4. How it works

Seven parts. Each says what has actually run. The picture version is the dashboard's [How it works page](https://zzfadi.github.io/geotech-robotics-hackathon/design.html).

1. **The physical arm, through our own SDK controller.** One right Maker Arm. `arm/tools/pickup_server.py` is a guarded holding controller on top of the pinned MakerMods SDK. It guards speed, heat, torque and stale feedback, and it freezes instead of dropping. The can routine in [`handoff/`](../handoff/README.md) moves in short steps, about 3° of joint travel each, and asks the turn gate before every step. Dream is not used on the real arm. The operator on the hardware e-stop is the safety stop; the software pauses are extra. *Status:* the same controller picked up a cylinder on Saturday. Before Sunday, the can routine had run in replay only. [Add the morning trial result here.]
2. **The local detector and the turn gate.** YOLO26n and YOLO26n-pose turn each camera frame into simple moments: a hand came in, a can is offered, the can is steady, the can was released. The operator's go key starts the routine, and one turn gate at the arm checks the detector before every step. A hand in the arm's path or a stale or failed camera frame pauses the arm before its next step. In hand mode the arm closes only when the can sits between the jaws with a hand on it. It carries the can away only after the camera has seen the hand off the can; if it never sees that within 10 s, it opens and gives the can back. *Status:* real models on still images and a replayed clip on the Mac mini; on the Jetson, 26–41 ms per frame on TensorRT FP16 engines, and a live webcam loop at the camera's 15 fps with nobody in view. The routine with its gate in replay only. [Live camera result with a hand and the can, if any.]
3. **The Dream API driving simulated arms on parallel Unreal tables.** One Unreal world holds nine Maker Arm tables in a 3×3 grid, run from the Luna2 Dream panel. Saturday's earlier ten-table batch ran its physics on the Mac, with poses mirrored to Luna2. Each table has its own Dream session: virtual cameras send frames, Dream returns actions, and our runtime moves the virtual arm. The arms use the official MakerMods model files. This is an editor kinematic representation with approximate contacts, not a calibrated digital twin. *Status:* runs in simulation. One Dream lift with grasp assistance off (from a controlled start) and one assisted cup batch passed their goals; the ten-table batch did not (results table).
4. **Webcam and Quest capture bring real people into the simulation.** A browser webcam or a Quest headset streams a person's pose into Unreal through Live Link Hub. In Quest passthrough the wearer saw the virtual scene and the real room together; the virtual table is not yet measured against the real one. In a test with spoofed poses, Isaac Sim on Luna2 received the same poses Unreal used, in a temporary test world. *Status:* webcam and Quest 2 streams worked on Saturday. Live capture of two people at once and table registration still need an on-site test. The live Isaac → Unreal physics stream is not built.
5. **The control center.** One read-only web page on 127.0.0.1:8780: what the arm is doing, whether it is healthy, and why it made each decision. It never commands the arm. *Status:* fed by simulated and replayed sessions, and served from the Jetson through an SSH tunnel with its live-webcam sessions (nobody in view). [Arm trial sessions, if wired by the demo.]
6. **A small edge model, in simulation only.** A 19,335-parameter policy, trained on our own scripted expert and never on Dream outputs. It is meant for the Jetson Orin Nano: keep moving when Dream's replies are late, and help a person whose aim is a few centimetres off. *Status:* scored on a simulated bench and a MuJoCo physics check, and run in simulation on the Jetson's CPU (0.038 ms per decision; MuJoCo 9 of 9 tables). Never on a real arm.
7. **Scripted voice, and LiveKit (sponsor).** 31 short lines in two arm personas, pre-rendered with macOS text-to-speech and played from a local speaker with no network. No speech recognition and no language model. An optional bridge publishes each station event and the spoken line into a LiveKit room so remote people can follow along. It can only publish; nothing in the room can move the arm. *Status:* tested on a local LiveKit dev server only; not yet heard on the stage speaker. [Cloud result, if Takeshi's setup worked.]

**The Jetson.** The Orin Nano was installed overnight and passed 140 offline arm tests at an earlier code revision (`d5c4f8d`), with no arm connected. It is not the arm's host today ([orin-nano-setup.md](../docs/handoffs/orin-nano-setup.md), [`4313e49`](https://github.com/zzfadi/geotech-robotics-hackathon/commit/4313e49)). Later that night it ran as a test hub, still with no arm or CAN adapter: the offline suites, the control center, the detector (on stills and its webcam) and the edge model (in simulation), with nothing moving an arm ([jetson-hub-testing.md](../docs/jetson-hub-testing.md)).

**Sponsors used.** MakerMods: the arm, its SDK and its model files. Dream Labs: the Dream API, in simulation only. LiveKit: the voice and event bridge, local dev server only unless the form row above says otherwise.

## 5. Verified results

Types: **physical** = real hardware; **simulation** = a simulated scene; **replay** = recorded or scripted inputs through the real code, no live hardware; **mock** = made-up or spoofed data. Results from different rows are separate runs. None of them shows a skill moving from the simulation to the real arm.

| Claim | Evidence | Type |
| --- | --- | --- |
| [Sunday live beat: taped mark X of 10, hand Y of 10, with prop, start pose and who held the can] | [dashboard post for the 11:00 call]; `python3 -m handoff.trials` (the trial log in `arm/local/` is not in git) | physical. Placeholder: leave it out if no trial ran |
| The right arm picked up an upright yellow cylinder, lifted it about 6 cm, placed it back and returned to verified rest. One supervised run, replaying measured joint waypoints found by wrist-camera servoing, not calibration. Timing is unsettled: the evidence JSON records 16 s from enable to lifted, while the commit message and arm/README.md give 16 s for the whole run. Until FADI confirms, say "lifted within 16 s". Not a can, not from a hand | [`dde9844`](https://github.com/zzfadi/geotech-robotics-hackathon/commit/dde9844), [arm/README.md](../arm/README.md), [evidence JSON](../arm/evidence/2026-11-07-cylinder-pickup.json) (field `fast_run_seconds_enable_to_lifted`), [photo](../docs/images/maker-arm-right-cylinder-lift.jpg) | physical |
| The can routine passes 20 of 20 replay scenarios on a fake arm, including a hidden hand and a covered lens; 50 hand-off tests, 144 arm tests and 17 control-center tests pass. The arm server claims its port before it enables the motors | [`b66503a`](https://github.com/zzfadi/geotech-robotics-hackathon/commit/b66503a), [handoff/README.md](../handoff/README.md) | replay |
| The real YOLO26n and YOLO26n-pose models ran on 5 still images (4 photos, 1 drawn) on the Mac mini (M4): about 48–53 ms per frame for both on the CPU, 22–26 ms on the Apple GPU. On a 3 s clip made from stills, on the Apple GPU, frame latency was 24.3 ms median and 35.4 ms p95, excluding camera capture. Not on the Orin, not a live camera, not the demo can | [`132ee2e`](https://github.com/zzfadi/geotech-robotics-hackathon/commit/132ee2e), [vision-ultralytics-mac-2026-11-08.json](../docs/validation/vision-ultralytics-mac-2026-11-08.json) | replay (still images) |
| The Dream response-time probe and its 850 ms "Dream or local" rule are built. They have run only with made-up timings, because the Dream key was not on the Mac mini when they were written | [`132ee2e`](https://github.com/zzfadi/geotech-robotics-hackathon/commit/132ee2e), [hybrid-model.md](../docs/hybrid-model.md) | mock |
| Dream drove a simulated arm that lifted a 5×5×12 cm red block about 11.2 cm and kept it above 10 cm for 5.9 s, with gravity on and no attachment: nine Dream calls, no scripted grasp. One run, from a controlled start: the jaws started open around the block | [maker-arm-camera-trial.md](../docs/maker-arm-camera-trial.md#verified-dream-driven-pickup--1547-pacific), [review JSON](../docs/validation/dream-tall-block-success-review.json) | simulation |
| Ten parallel simulations (on the Mac, block-lift tasks), 84 Dream attempts in about 170 s (49 returned actions, 34 session renewals, 1 recovered capacity refusal). An attempt is a request, not a pick. Seven of the ten arms moved. **No station passed the 10 cm held-lift objective in that batch.** At most four requests were in flight at once | [maker-arm-fleet.md](../docs/maker-arm-fleet.md), [per-table outcomes](../docs/validation/fleet-parallel-trial-2026-11-07.json) | simulation |
| **Assisted:** 8 of 9 tables held a cup at their random 16–27 cm targets for 5 s (batch 26, measured by the hold check). Dream chose the grip and the lift moves. The assistance: a scripted reach to the cup, a grasp angle our code picked per table, the arm held still while Dream closed the jaws, a grip latch, a tabletop guard, a scripted re-reach if a cup dropped, a friction-grip model standing in for real finger friction, and, while holding, only Dream's upward motion applied, scaled 2.5× and never lowered. The log says a video review is still needed before calling these grasps | [`7be1a11`](https://github.com/zzfadi/geotech-robotics-hackathon/commit/7be1a11), [cup iteration log](../docs/validation/claude-cup-iterations-2026-11-07.md), [`DreamSim/fleet_cups_claude.json`](../DreamSim/fleet_cups_claude.json) | simulation, assisted |
| Edge model alone held the cup in 172 of 180 held-out attempts; the scripted expert it learned from held 167. Within noise (p = 0.125) | [`7ac2e28`](https://github.com/zzfadi/geotech-robotics-hackathon/commit/7ac2e28), [edge-model-bench-2026-11-08.json](../docs/validation/edge-model-bench-2026-11-08.json) | simulation (kinematic bench) |
| A simulated operator aiming 2–4 cm off held the cup 18 times in 180 alone and 162 times with the model's assist | same JSON | simulation (kinematic bench) |
| With simulated late replies, 173 holds when the model fills the gaps vs 170 when the arm waits (within noise). Frozen time was 0.27 s vs 1.30 s per attempt, but most of that gap is the wait for the first reply. The replies came from the scripted expert, not Dream | same JSON | simulation (kinematic bench) |
| MuJoCo physics: the expert held the lifted cup on 18 of 18 tables and the model on 17 of 18 with grasp-friendly contact settings; the expert held 0 of 18 with MuJoCo's default settings | same JSON | simulation (physics, assumed cup and gains) |
| A live Mac browser webcam published 65 poses in 65 frames, and Unreal evaluated the last one with 23 bones | [dashboard update 13:10 Sat](../dashboard/updates/2026-11-07-1310-mac-webcam-and-quest-browser.json), [Capture/README.md](../Capture/README.md) | physical (camera) into simulation |
| A real Quest 2 moved a MetaHuman's head and both wrists in Unreal; the wearer confirmed the real room and the virtual scene together in passthrough | [Quest 2 motion update](../dashboard/updates/2026-11-07-1409-quest2-local-performer-motion.json) (`3363df0`), [passthrough update](../dashboard/updates/2026-11-07-1643-quest2-room-overlay-confirmed.json) (`8ed1946`, `5e2c2d8`) | physical (headset) into simulation; visual check only |
| 4,369 spoofed poses from three Unreal performers matched in Isaac Sim, 107.6 ms median from Unreal to Isaac, including a 100 ms playout delay | [isaac-performer-bridge-2026-11-07.json](../docs/validation/isaac-performer-bridge-2026-11-07.json) | mock (spoofed poses) |
| The scripted voice: 31 lines rendered, 30 unit tests pass. A replayed hand-off sent 36 of 36 event messages and the voice track through a **local** LiveKit dev server. Not tested on LiveKit Cloud or a stage speaker | [`bb9326f`](https://github.com/zzfadi/geotech-robotics-hackathon/commit/bb9326f), [livekit-fit.md](../docs/livekit-fit.md) | replay (local server) |
| The Jetson Orin Nano runs Ubuntu 24.04.4 with L4T 39.2.1 from its SSD, takes SSH logins, and passed 140 offline arm tests in 2.77 s at code revision `d5c4f8d` (before the overnight arm-server changes). No arm or CAN adapter connected | [`4313e49`](https://github.com/zzfadi/geotech-robotics-hackathon/commit/4313e49), [orin-nano-setup.md](../docs/handoffs/orin-nano-setup.md) | physical (board only) |
| The Jetson as a test hub, no arm or CAN adapter attached: the offline suites pass (144 arm, 50 hand-off, 20 of 20 replay scenarios, 17 control-center, 49 vision tests), and the control center serves through an SSH tunnel. YOLO26n + pose on its GPU: 26–41 ms per frame on TensorRT FP16 engines over the same 5 stills (5.3–5.7 ms inference per model). A 30 s live loop on the C270 webcam ran at 15 fps, the camera's own rate in a dark room, with nobody in view, so no hand or can was seen | [`0be16ee`](https://github.com/zzfadi/geotech-robotics-hackathon/commit/0be16ee), [jetson-hub-testing.md](../docs/jetson-hub-testing.md), [hub check JSON](../docs/validation/jetson-hub-check-2026-11-08.json) | physical (board and webcam only); stills are replay |
| The trained edge model on the Jetson's CPU: 0.038 ms per decision (p99 0.05 ms) against a 200 ms control step. In the MuJoCo physics bench there, both the expert and the model held the cup on 9 of 9 tables (one seed, the grasp-friendly contact settings). On the Mac the model missed one of those tables, so single-table physics results are indicative only | [`19c5ae4`](https://github.com/zzfadi/geotech-robotics-hackathon/commit/19c5ae4), [edge-model-jetson-2026-11-08.json](../docs/validation/edge-model-jetson-2026-11-08.json) | simulation (on the Jetson CPU) |

## 6. What failed and what is next

**What failed** (keep these; the rubric rewards them):

- **The left arm missed.** Its jaws closed above the cylinder. It has a different claw and different joint offsets, so we use the right arm only ([arm/README.md](../arm/README.md)).
- **Slow, careful moves overheated the arm.** Two earlier right-arm attempts stopped on the 48 °C and 52 °C heat guards before the lift. Fast replay of measured waypoints stayed cooler ([evidence JSON](../arm/evidence/2026-11-07-cylinder-pickup.json)).
- **The ten-table batch lifted nothing to the goal.** No station held its block 10 cm up; the best peak was 4.2 cm. At three of the ten tables our runtime rejected Dream's requested hand orientation before the arm moved. The first ten-table run also hit five capacity refusals ([maker-arm-fleet.md](../docs/maker-arm-fleet.md)).
- **The simulated gripper can't really grip.** Without the friction-grip model, the finger pads carried the cup only about 20–30% of the time, whoever drove the arm. Most early batches were 0 of 9. Even the best batch missed one table: it held the cup at 21.8 cm against a 25 cm target ([cup iteration log](../docs/validation/claude-cup-iterations-2026-11-07.md)).
- **The detector needs tuning on the real camera.** Hand boxes sit on the wrist, not the fingers. The yellow cylinder read as "bottle" at 0.33, just under the 0.35 cut-off ([vision check](../docs/validation/vision-ultralytics-mac-2026-11-08.json)).
- **Physics settings decide the grasp.** In MuJoCo the same grasp held 18 of 18 or 0 of 18 depending on contact settings, so simulated grasp numbers can't stand in for the real gripper ([edge model bench](../docs/validation/edge-model-bench-2026-11-08.json)).
- **The arm can't stop mid-step.** The arm server has no hold command, so a hand that appears during a step stops the next step, not the current one: up to about 0.4 s of motion ([handoff limits](../handoff/README.md#limits)).
- **LiveKit joins were slow on the Mac:** 30 to 49 s across two runs on a local dev server, and some earlier joins timed out. The Mac's firewall is the suspected cause; Cloud join time is not measured ([livekit-fit.md](../docs/livekit-fit.md)).
- **The Jetson install failed twice overnight** before it booted from its SSD ([failure 1](../dashboard/updates/2026-11-08-0121-jetson-install-failed-diagnostic-shell.json), [failure 2](../dashboard/updates/2026-11-08-0221-jetson-bootloader-repair-and-tailscale-cache.json)).
- [Sunday trials: what failed, with counts, e.g. "hand mode reached X of 10, so we demoed the mark".]

**What is next:**

- The hand-off from a person's hand on the real arm, measured over at least 10 trials [delete if it passed].
- A real hold command in the arm server, so a hand stops the current step.
- A real Dream round-trip measurement, to drive the 850 ms "Dream or local" rule.
- The edge model on the real arm, behind the arm server's guards; so far it has run on the Jetson in simulation only. Train on Dream runs only after checking Dream's terms ([edge-model.md](../docs/handoffs/edge-model.md)).
- Fingers in the simulation that grip with real physics, or a grasp constraint. That needs a team decision, because the current rule forbids attaching the cup.
- The live Isaac → Unreal stream, and the simulated hand-to-arm contact trial on Luna2.
- From the deck outline (slide 12; the spoken script words it differently): infer what a person means and smooth their input, bring more remote people into parallel rehearsals, then test whether a skill learned in simulation transfers to the real arm.

## 7. Team

From [`dashboard/data/project.json`](../dashboard/data/project.json):

| Name | Role |
| --- | --- |
| FADI | Lead, robot stack |
| Brian Frager | GeoTech, XR + capture, pitch |
| Denys Dubinin | GeoTech, Unreal + Dream simulation |
| Saleh Aldwais | Arm hardware integration |
| Takeshi Okamura | Cameras, LiveKit + UI |

## 8. Links

- Repository: https://github.com/zzfadi/geotech-robotics-hackathon
- Team dashboard: https://zzfadi.github.io/geotech-robotics-hackathon/
- How it works: https://zzfadi.github.io/geotech-robotics-hackathon/design.html
- Every update: https://zzfadi.github.io/geotech-robotics-hackathon/updates.html
- Judges deck: [Keynote](pitch/deck/The%20Human%20Touch%20%E2%80%94%20Judges.key), [PDF](pitch/deck/The%20Human%20Touch%20%E2%80%94%20Judges.pdf), [PowerPoint](pitch/deck/The%20Human%20Touch%20%E2%80%94%20Judges.pptx), [spoken script](pitch/deck/judges-script.md), [station run card](pitch/deck/judges-run-card.md), [rubric notes](pitch/deck/creative-rubric.md)
- Pitch art and media cues: [demo/pitch/README.md](pitch/README.md). The museum scenes are concept illustrations, not hardware footage.
- Plan and run of day: [docs/plan.md](../docs/plan.md)
- Demo video: [VIDEO URL]

## 9. Claims we must not make

Check every line before you submit. If the form text or the video narration says any of these, cut it.

- [ ] The arm took the can from a person's hand. Only if hand mode reached 8 of 10 at 11:00. "From Brian's hand" only if a run was also done with Brian.
- [ ] Any Sunday trial rate we didn't count with `handoff.trials`.
- [ ] Dream drives the real arm. It drives the simulated arms only. (FADI reports a physical Dream-interface test outside this repo on Nov 7; nothing about it is recorded here, so don't cite it.)
- [ ] LiveKit Cloud is in use, or the voice is a live AI agent. It is pre-rendered text-to-speech lines, plus a local-server bridge, unless the form row says Cloud worked.
- [ ] Real people rehearsed with the Dream-driven arms. Capture and the Dream tables have run separately, not together.
- [ ] The whole cylinder run took 16 s. Say "lifted within 16 s" until FADI confirms the timing.
- [ ] The Quest overlay is aligned with the real table. The wearer saw both together; the alignment was not measured.
- [ ] The Jetson runs the arm. It has no arm or CAN adapter connected.
- [ ] A calibrated digital twin. The Unreal arms are an editor kinematic representation with approximate contacts.
- [ ] A measured Dream response time. Only made-up timings have run.
- [ ] "8 of 9 cups" without the word "assisted" and the word "simulation". It was one batch, with a friction-grip model.
- [ ] The ten-table batch picked things up. No station passed the held-lift objective there; "84 attempts" are requests, not picks.
- [ ] The edge model runs on the real arm or drives any hardware, or beats the expert. It has run only in simulation (on the Mac and on the Jetson's CPU), and it is within noise of the expert.
- [ ] The YOLO detector has seen a hand or the can on a live camera. The Jetson's live webcam run had nobody in view; its hand and can results so far are on still images and replays.
- [ ] Skills moved from the simulation to the real arm. Every result is a separate run.
- [ ] A live Isaac physics stream into Unreal. It is not built.
- [ ] The software pause or turn gate is a safety system. The operator on the e-stop is the safety stop.
- [ ] The fist bump works. Only if it was taught and kept at 11:00.
- [ ] The cylinder pickup was a can, a cup, or repeated. It was one supervised run with a cylinder.
- [ ] The museum films are real footage.
