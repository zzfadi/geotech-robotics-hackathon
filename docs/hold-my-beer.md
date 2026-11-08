# TheHumanTouch: Hold My Beer

## Current goal

The final hackathon pitch centers on a robot accepting the task “Hold my beer” and grasping and steadily holding a drinking cup in simulation. On November 7, 2026, the user explicitly replaced the block-pickup objective and confirmed **beer cups / drinking cups**, not bottle caps.

Replace the blocks, rods and unrelated tabletop equipment boxes with 4–5 drinking cups per table across all nine tables. Randomize their positions while keeping cups upright, separated and within the intended workspace. Preserve the accepted 3×3 open-room layout, Maker Arm geometry, table numbers, soft lighting, virtual cameras and Multi-User session. Table 1 must be visible and selectable in the simplified web panel, alongside single-table and all-nine execution. Retain a simple Hold My Beer prompt, Run/Stop, cup-layout shuffle and live/recorded views.

## First demonstrable milestone

- Four or five empty, upright drinking cups per table; geometry and collision representation must be documented. Select one cup as the task target; the other cups remain physical obstacles.
- A real Dream request drives the selected arm toward the cup, closes the jaws, lifts it at least 10 cm and holds it for five continuous seconds.
- Measure cup height, orientation and motion during the hold. Keep gravity enabled; do not attach or teleport the cup to manufacture success.
- Show current camera frames in the web panel, retain a playable recording and verify the cup is actually retained. A transient height spike or a completed API request is not task success.
- Verify scene and movement on accessible connected Unreal editors, and distinguish unobserved clients from verified ones.

The five-second hold is the implementation target for this first demo. Upright tilt and low-motion thresholds must be recorded with each test rather than inferred from a success label. Begin with one table; run all nine only after the cup setup and previews are verified.

## Fidelity and training status

Initial cups are empty. Liquid physics, spill prevention, calibrated contact forces and physical-hardware transfer are outside the evidence available so far. A visually convincing empty-cup hold does not prove a filled beer can be handled without spilling.

Running Dream inference and evaluating repeated attempts is simulation practice/evaluation, not model-weight training. Actual fine-tuning requires a suitable uploaded robot dataset, a submitted training job and a published checkpoint. No cup fine-tuning job or trained cup policy is claimed here.

## Evidence preservation

Retain the earlier block-trial recordings and measured failures as historical engineering evidence. Do not relabel them as cup trials. New cup results should identify the request ID, model, scene/config revision, object dimensions, hold criteria, video and measured result. Save guarded Unreal checkpoints and push focused source changes to GitHub.
