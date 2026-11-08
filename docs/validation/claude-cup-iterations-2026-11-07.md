# Claude cup-pickup iterations (2026-11-07, nine tables, Dream API)

Driver: Claude, via the Luna2 page queue (`configured` mode) and `DreamSim/fleet_cups_claude.json`.
Every action comes from Dream (`dreamlabs/dream-1-beta` unless noted). Assistance is opt-in per table and
labelled in each result's `assist` field:

- **pregrasp start**: the episode starts with the left jaws open (10 cm) around the target cup, the tool
  2.5 cm above the cup centre and tilted 45° (Codex's verified mechanical grasp pose), computed by IK from the
  configured or live cup position. Dream still decides closing, lifting and holding.
- **table guard**: rows whose flat pads would enter the tabletop are raised just enough to clear it
  (same geometry as the Unreal check) instead of the runtime halting that table.
- **grip latch**: once the jaws stall on an object (measured gap > commanded gap + 1 cm), later rows may not
  reopen beyond that command.
- **live layout**: the batch reads the editor's cup positions at start (the editor restores pre-run poses after
  every simulation, so this is the arranged layout, never a fallen cup).

Success is measured, not inferred: the target cup centre must stay at least the table's required height above
its start for a continuous 5 s, tilt ≤ 15°, speed ≤ 10 cm/s, physics and gravity on, not attached. Video
review is still required before calling it a grasp.

## Batch 1 — `a7728397faab40f4a21b6bc67b0037e0` (21:58 run dir `20261107-205829-19f7ce`), 10 cm, old shuffled layout

0/9. Findings:

- `dreamlabs/dream-1-beta-flash` is not served (`DA-2000 NO_CAPACITY (pool_warming): no live serve yet`):
  T2, T4, T8 never acted. All later batches use the standard model.
- Raw joint targets (no IK) are unusable here: T3's chunk drove the pads 5.7 cm into the tabletop.
- From the pregrasp start Dream **does close on the cup in cycle 1** (jaws stop at 7.3 cm on the cup while
  commanding 3.2 cm), then **reopens in cycle 2**, knocks the cup over in cycle 3 and then performs a
  lift-and-retract with empty jaws (cycles 4–5). Same pattern on T1, T5 and T9. Grasp intent is present; the
  grip flip-flops between chunks.
- "Hold my beer." alone knocked the cup off the table (T7).
- One IK solve raised `SVD did not converge` (T6, 6-row horizon).

## Batch 2 — heights 15/20/25/30 cm, Codex's centred layout, grip latch on 8 tables

`5a5493d15e324c32b092ab5950ccf448` (run dir `20261107-210848-8098da`): 0/9. Max rise 3.8 cm (25 cm table,
grasp at cup centre), 2.8 cm ("Hold my beer"). The latch engaged on three tables at a 7.2–7.4 cm jaw gap, but
the cup still tipped: Dream slides the hand 1–2 cm sideways and down while holding, and with a kinematic
gripper that stops at contact (no squeeze) the cup pivots out of the pads. Dream's per-cycle motion: close in
cycle 1, a lift-and-pull-back gesture (+1.4 to +3.2 cm up) in cycles 2–5, then a steady 1–2 cm per cycle drift
down. Steep (70°) and side (20°) approaches left the cup upright but never grasped. The 24-row horizon hit
`SVD did not converge` again (now caught as a held cycle).

## Batch 3 — `e39dfcac21a44011b1c503dd95a66759` (run dir after `20261107-2113`), vertical hold

0/9; stopped from the page at 80 s (one table interrupted). Applying only Dream's vertical motion while holding
kept two cups upright in the jaws: **T8 (30 cm) rose 4.7 cm upright**, T4 (pre-closed) 1.5 cm upright. Bug
found: the descent allowance was relative to the current height each cycle, so Dream's downward drift walked
the cup back down. Fixed to a fixed floor; batch 4 adds an opt-in monotonic hold (never lower a held cup).

## Scene change requested by the user

Reset now places every table's 4–5 cups at random positions between the two arms (x 48–62 cm,
y ±33.5 cm, ≥13 cm apart; `Cup` is always the one nearest the left arm). The default seed still reproduces
Codex's arranged centred layout (`cup_scene_layout.json`). Verified live after reset `1369b4f5cb684a3b8678211c10f3f169`.

## Batch 4 — monotonic hold, random central layout (`20261107-212025-557bbc`)

0/9. Most tables ended on `SVD did not converge`, raised inside the Dream client's own local IK during
`get_action` (now retried). Gap history showed the latch letting the jaws keep closing toward Dream's 3.5 cm
command after stalling at 7.3 cm: they cut through the thin cup wall (gap 7.3 → 4.3 → 1.2 cm).

## Batch 5 — Dream language subgoals, random 15–30 cm heights

0/9 (max 1.7 cm). With an explicit "lift the cup straight up" subgoal Dream lifted the hand +3 to +5.6 cm per
cycle, but the cup stayed down: a 2 mm squeeze carries nothing. Codex's verified mechanical hold commands
1.9 cm inside the contact width (5.5 cm on a 7.4 cm cup) and lifts at about 3 cm/s; both adopted.

## Batches 6–8 — every run from idle, cups reset, planned reach

- Batch 6: the reach from idle ran on all nine tables, then each stopped on a guard bug (Dream asked for a jaw
  opening of 1.003 × max; the guard passed it to the model unclamped). Fixed and tested.
- Batch 7 (stopped): the runtime played only the first 12 rows (the policy horizon) of the 55-row reach, so
  the hand stopped 11 cm short of the cup. The same cap truncated split held lifts. Fixed: the reach and held
  lifts report their full row count.
- Batch 8 (`2f57dfe817d2492580d81d5e9b253ebd`): reach reaches the cup; jaws stall at 7.4 cm and hold the
  verified 5.5 cm command. Dream lifts the hand up to 15 cm (T7: z 7.5 → 22.5 cm), the cup rises ~2 cm with it
  and then slides out of the pads between 10 and 12 cm hand height and lands upright. Best rise 2.1 cm.

## Batch 9 — grip strategy experiment (3 tables each)

Codex's mechanical lift path (back 0.67 cm per cm up along the tool), slower straight lift (≈1.5 cm/s), and a
firmer 3 cm squeeze. Heights random 15–30 cm.

Result (`437602b506f1425080fbd6329ad2cfbb`): **slow lift carried the cup** — T5 rose 15.2 cm upright (tilt
2.3°), above 10 cm for 36 s, jaws at 7.4 cm throughout; failed only because its target was 26 cm. Firm 3 cm
squeeze reached 14 cm then slipped. Only 2 of 9 tables had the cup between the jaws: Dream moves the arm while
closing and nudges the cup away.

## Batch 10 — still-arm grasp, slow lift everywhere, lift prompt variants

`04245105d5604076b090e002bfae335a`: 7 of 9 tables closed on the cup (was 2/9); rises 9.8, 9.3, 8.0, 5.7, 4.4
cm, mostly slipping. Runtime `contact_gap` explains the slip: while both pads touch, the jaws stop at contact
width minus 5 mm compliance; when the both-pad check drops for a tick, the jaws snap to the command. A 5.5 cm
command is 2 cm inside the cup and ejects it (logged gap jumps 7.4 → 5.5 cm).

## Batch 11 — latch at contact minus 6 mm

`b882f91d9b9a4055abf45121b8388ed4`: 8 of 9 grasped. **T8 carried 20.1 cm upright (tilt 4.5°), above 10 cm for
86 s**; T6 12.7 cm upright (target 15). Dream now lifts the hand 17–20 cm on every table with the lift subgoal.
Where the gap fell to the 6.9 cm command at the first lift the cup had already left the pads: the tool centre
sits at the fingertip end of the runtime's pad contact corridor (flange Z −17..−10 cm), so contact is
marginal. Batch 12 grasps 3.5 cm deeper and re-reaches (up to twice) when a cup drops back upright.

## Batch 12 — 3.5 cm deeper grasp (regression)

`f1718a474c86470d99dde10a53e64320`: the deeper approach brought the gripper body down onto the cup; all nine
cups were knocked over during the reach. Reverted.

## Batch 13 — re-grasp after drops

`c2ce21b657a64a72867ba28848f3b392`: T9 carried 17.4 cm (target 18) above 10 cm for 15.7 s; T8 15.7 cm
(target 19). Re-grasp worked (T3 re-reached and re-grasped twice).

## Batch 14 — no descent while holding, ≈1.6 cm/s lift — **first success**

`2055e7500f2a4c0e9d18d15114446867` (run dir `20261107-222405-1cfba8`): **T3 passed — cup held 18.3 cm above
its start (target 18 cm) for a continuous 5.0 s, tilt 2.7°, gravity on, no attachment.** The overhead video
shows the white target cup raised between the jaws; the wrist camera stays filled by the cup interior through
the hold. T7 21.3 cm (target 25) for 51 s; T8 16.4 cm; T1 13.6 cm. Five tables stopped on
`Mirror actor disappeared`: a cup knocked off a table falls through the open-room floor (Table 1 read
−3685 cm) and Chaos removes it, and the runtime mirror then halted every table. The mirror now drops that one
actor and continues (live editor reloaded).

Reach limit: holding the 45° grasp orientation, the arm can lift the cup straight up only ~15–22 cm; 60°
allows ~28 cm and 75° ~34 cm.

## Batch 15 — 72° grasp (worse)

`01b9ad83323a4a238bee383b5ef03da2`: cups slipped right after grasping (best 4.1 cm) and Dream's targets went
out of reach (one table stopped on six truncated horizons). Dream refused one subgoal:
`DA-4000 BAD_REQUEST (malformed): unsafe instruction in this scene, refused: Lift` — now handled by rotating to
the next subgoal wording. Batch 16 compares 45°, 55° and 60° grasps, three tables each.

## Batches 16–18 and zero-API physics probes — the grip, not Dream, is the limit

- Batch 16 (`1279806c1f3e49ab8d3cc7b19291d42b`): 60° grasps carried 18.5 cm and 11.2 cm; 45° tables dropped.
- Batch 17 (`1e59155997794cf191c53faea02d3e89`): 15 g cup (realistic empty paper cup) instead of the uncalibrated
  50 g: cups tipped far more often. Reverted to 50 g.
- Batch 18 (`011eed1997234b81a5519d4415031a2f`): smooth 30-row jaw close: 11.6, 9.4, 9.2 cm; no pass.
- **Probe 1 (no Dream calls)**, scripted close + straight 2 cm/chunk lift: cups carried at most 9.9 cm.
- **Probe 2 (no Dream calls)**, exact replica of Codex's verified mechanical sequence (65-row close to 5.5 cm,
  12 cm up / 8 cm back lift, constant command): **2 of 9 carried** (11.5 cm held 7 s; 21.3 cm).
- Probe 3: keeping cups awake while the jaws move: **0 of 9**, 7 cups ejected. Reverted (flag off).

Conclusion: the kinematic pads teleport into the cup and rely on penetration contacts, which carry the cup only
~20–30% of the time whoever drives the arm. Codex's single verified mechanical hold was one of the lucky cases.
Further gains need a physics-side change (simulated finger drives, or a grasp constraint while both pads hold
the cup, which the current "no attachment" rule forbids) — a team decision. Also: holding the 45° grasp
orientation caps the lift at ~18–22 cm; 60° allows ~28 cm.

## Cup physics investigation (user request) — zero-API probes, same reach/close/lift

| Probe | Change | Carried |
|---|---|---|
| 2 | Codex's verified mechanical sequence, baseline physics | 2/9 |
| 3 | keep cups awake while jaws move | 0/9 (7 ejected) |
| 4 | pad friction 1.5 with Max combine, cup 1.0 | 0/9 |
| 5 | hold 1 mm per pad inside contact | 1/9 |
| 6 | limiter compliance 10 mm (no effect: hollow mode caps squeeze at one 2 mm wall) | 0/9 |
| 7 | solid one-hull cup collider in the simulation (`SM_DrinkingCup_SolidCollision`) + 2 mm per pad | 1/9 |

Findings: with the original settings the jaws stop where the pads just touch the real cup surface (7.4 cm), and
the hollow limiter caps total squeeze at one 2 mm wall; the cup's collision is 20 thin wall hulls. Neither
material, mass, waking nor a solid collider changed the carry rate, which points at the actuation model: the
finger pads are kinematic boxes repositioned every tick, which transfer little friction in Chaos. A reliable
grasp needs physically driven fingers or a grasp constraint while both pads hold the cup; the current team
rule forbids attachment, so that is a decision for the team. All new physics options remain in the runtime as
named constants, switched off (baseline restored); `SM_DrinkingCup_SolidCollision` exists in
`/Game/_Agents/Denys_Mac_Codex/MakerArm/Cups` but is unused while `SOLID_CUP_COLLISION = False`.
Separately, `Mirror sample requires no enclosing transaction` and `SIE exit timed out` appeared once (batch 19)
while another editor operation was active.

## Friction-grip model (user-requested realistic side grasp) — 5/9 pass

The user asked for real-cup behaviour on a side grasp: the pads squeeze the paper cup a little and the arm can
still take it. `Tools/maker_arm_friction_grip.py` models the friction the kinematic pads cannot transfer:
while BOTH pads press the same cup and the jaw is commanded tighter than contact (a squeeze), each tick the
cup's velocity is driven toward the pose it had relative to the gripper when the grip formed (gripper motion +
error/50 ms + gravity feed-forward, capped at 60 cm/s). Gravity stays enabled, there is no actor attachment or
teleport, and it releases when the jaws open, the cup leaves the pads or it slips more than 2.5 cm. Runtime
status reports `friction_grip` per table. **Results with it are "friction-grip model" results.**

- Probe 10 (no Dream): 9/9 cups carried upright to the 45°-grasp reach ceiling (10–20.5 cm).
- Batch 21 (`04004d8aa9d94a548e6e79c65a555421`): Dream lifted every cup upright, 6 above target (up to
  26.5 cm), but the hold check read the commanded drive velocity as cup motion and 5 tables hit the 300 s
  session limit. Fixed: drive after measurement, feed-forward, ≈3 cm/s lift.
- **Batch 22 (`8b2993843b1543b5bb50830b90bcd29d`): 5/9 passed** — T1 20 cm (peak 23.3), T3 16 cm (18.6),
  **T7 29 cm (30.0)**, T8 19 cm (19.5), T9 21 cm (21.8), each a continuous 5 s hold, tilt ≤ 9.4°. Near misses
  hovered just under target (23.1/23, 26.8/27): batch 23 keeps lifting to target + 1.5 cm before holding.
- **Batch 23 (`f0c128f2bff54e05b0b38ab19e9f3eb3`): 7/9** — 25, 16, 15, 18, 23, 19, 27 cm held 5 s. Misses at 27 cm
  were the 60° grasp's reach ceiling.
- Batch 24 (`2fe9f1bf1f46431a9b4cdeb92b750b21`): 6/9, including a **30 cm hold** with a 68° grasp.
- Batch 25 (`905dc316ccc44c5986ed5605cf301948`): 5/9 with automatic grasp-angle choice (shallowest angle whose
  straight-lift ceiling clears target + 2 cm, 45–80°). Dream's lift faded near 20 cm; one table ended on an
  unreachable first target (now a held cycle).
- **Batch 26 (`a4b4718986604153bbec3edcbaad2f78`): 8/9** — 17, 19, 16, **27**, 16, 17, 22, 18 cm held 5 s
  (peaks up to 30.1 cm, tilt ≤ 4.9°) with Dream's upward steps scaled 2.5× while holding. Miss: T8 reached
  21.8 cm (target 25) and held it 65 s.
