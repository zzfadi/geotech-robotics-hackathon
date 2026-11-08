# Team plan: Hold My Beer (updated Saturday night, Nov 7, 8:35 PM)

This is the working plan for Sunday, rebuilt from Brian's **Day 1 EOD sync** (Brian and Denys, posted in #geotech-robotics at 7:04 PM) and the repo as it stood at 8:10 PM. The history of how we got here, including every Saturday decision, is in [the Saturday Slack log](slack-log-2026-11-07.md). Where tonight's notes change an earlier decision, it is marked **Supersedes**.

All hackathon work, plans and handoffs live in this repo only; the product-factory repo is not used for it (FADI, 8:28 PM). Credentials stay out of this file. The Dream key is the `DREAM_API_KEY` environment variable from the team password manager.

## Needs a decision

Nobody owns these yet, and nothing below assigns them. Settle them first thing Sunday at the latest.

**Top two**

1. Who owns the beer handoff on the real arm: take a can from a person's hand, then the fist bump.
2. How Dream-1 and Codex split the real arm: which moves Dream-1 drives and which Codex scripts directly.

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
- Ten parallel simulations or nine? Nine run today.
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
| 5 | Physical arm takes a handed-over can from a person, then fist bump | **No owner named** (FADI built the current pickup) | Before rehearsal |
| 6 | Hold interlock: Isaac → LiveKit hold message into our SDK controller, dry-run tested | **No owner named** (earlier: Saleh and FADI) | Only if kept in the MVP |
| 7 | Isaac → Unreal stream over LiveKit | **No owner named** | Unclear if needed for the MVP |
| 8 | LiveKit voice agent (Brian called it a must-have on Saturday) | **No owner named** (earlier: Takeshi) | Unclear if still in |
| 9 | Run the Luna2 simulated hand-to-arm contact trial | **No owner named** | Sunday |
| 10 | Reserve 2 hours for submission and run it | **No owner named** | Before the cutoff |
| 11 | Jetson robot server, end to end ([brief](handoffs/jetson-end-to-end.md)) | FADI, working on the Mac Mini from this repo | Tonight; not required for the MVP |
| 12 | Train and score a small edge model for the Orin Nano: Dream lag bridging and auto-assist, no Dream fine-tune ([brief](handoffs/edge-model.md)) | FADI, working on the Mac Mini from this repo | Tonight, time-boxed; not required for the MVP |

## Handoffs for the Mac Mini

Two workstreams need the Mac or hardware. FADI owns both and works them on the Mac Mini from this repo, with whichever agents run there; the briefs are written so a fresh agent can start cold:

- [Jetson robot server, end to end](handoffs/jetson-end-to-end.md)
- [Train a small model from our simulation](handoffs/small-model-training.md), now aimed at [the edge model](handoffs/edge-model.md)
- [Dream workshop takeaways](handoffs/dream-workshop-takeaways.md)

