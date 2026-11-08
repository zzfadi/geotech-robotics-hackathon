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
