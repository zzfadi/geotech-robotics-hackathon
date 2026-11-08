# edge_model (pipeline + bench scores)

Code for the [edge model handoff](../docs/handoffs/edge-model.md): collect scripted-expert demonstrations on a kinematic bench, train a small numpy policy, and score it in the three scenarios on held-out scene seeds, writing control-center telemetry for every attempt. Nothing here moves a physical arm or calls the Dream API. **Every number below is a simulation result, not a physical one.**

## Run it

numpy 2 and Python 3.10 or newer (the Mac's system `python3` is 3.9 without numpy, so use `arm/.venv/bin/python`; on the Jetson, its Python 3.12 numpy env). torch is optional and not needed.

```sh
arm/.venv/bin/python -W ignore -m edge_model run-all --quick   # ~20 s plumbing check into edge_model/local/quick/ (undertrained model, not scores)
arm/.venv/bin/python -W ignore -m edge_model run-all           # full: 100 seeds, ~4.5 min on the Mac mini
arm/.venv/bin/python -m unittest discover -s edge_model -p 'test_*.py' -t .   # ~1 s
python3 -m control_center --data edge_model/local/sessions validate <session id>
python3 -m control_center --data edge_model/local/sessions serve              # replay attempts
```

Outputs go to `edge_model/local/` (git-ignored; `--quick` writes to `edge_model/local/quick/` so it never replaces the full run's checkpoint, and `--out DIR` picks any folder): `dataset/`, `checkpoints/policy.npz` (+ `.json` provenance), `eval/eval_summary.json`, `eval/eval_attempts.jsonl`, `sessions/` (one control-center session per attempt). Subcommands `collect`, `train` and `evaluate` run the steps on their own; `run-all --dagger N` adds N DAgger rounds (implemented, smoke-tested only).

Optional physics check (MuJoCo, kept out of the numpy runtime):

```sh
uv run --python 3.12 --with numpy --with mujoco==4.0.0 \
  python -W ignore -m edge_model.physics_bench --seeds 2 --policy edge_model/local/checkpoints/policy.npz
```

## Files

| File | What it does |
| --- | --- |
| `kinematics.py` | Pinned Maker Arm FK from `DreamSim/compiled_fk.py`, numpy-only IK, Unreal-to-arm-base frames |
| `bench.py` | **Kinematic** stand-in for one cup table: fleet layouts, friction-proxy grasp, palm/jaw/table contacts, recorder-shaped evidence (`physics_simulated: false`). Not physics |
| `expert.py` | Scripted expert: pregrasp, 20° pitched side approach, close, 12.5 cm lift at 4 cm/s, hold. Plans from a cup *estimate* |
| `gate.py` | Positive gate with the exporter's thresholds (10 cm clearance for 5 s, ≤15° tilt, ≤10 cm/s, both jaws, nothing else touching) |
| `model.py` | 19,335-parameter MLP: 14 inputs (6 joints, grip, tool xyz, cup offset, cup height) to a joint step and grip. Numpy inference |
| `collect.py` | Runs the expert over seeds × 9 tables × variants (clean; perturbed start + small joint noise in travel/approach, DART-style, labels stay the expert's). Gate-passing episodes are demonstrations, failures go to the eval set. Split by scene seed 70/10/20. Also DAgger relabelling (expert labels on policy-visited training-seed states, kept in its own file) |
| `dataset.py` | One `.npz` per split plus `manifest.json`: `source: bench-expert`, seeds per split, counts, sha256 per file, git commit, versions |
| `train.py` | `model.py`'s numpy trainer; checkpoint meta records config, data hashes, time, Python/numpy/platform, loss history, offline held-out error |
| `scenarios.py` | The three scenarios (below), 0.85 m/s commanded-speed clamp on every command |
| `evaluate.py` | Runs every row on held-out seeds, aggregates, validates the telemetry |
| `telemetry_log.py` | One `control_center.telemetry.Writer` session per attempt (component `edge-model`) |
| `physics_bench.py` | Optional MuJoCo cup table from the same URDF and meshes, built the way `Capture/contact_sim.py` builds its station |

## The scenarios

- **Autonomous:** the expert (reference), the same network with random weights (the "before" row) and the trained policy, each alone. The cup position comes from a noisy estimate (σ 3 mm), standing in for the overhead camera.
- **Lag:** a chunked teacher with Dream-like late replies. The teacher is the scripted expert, **not Dream**. A reply is a 48-action chunk (2.4 s; one 0.2 s bench step = 4 actions). A request carries the arm state and the already-committed commands, and the teacher plans the continuation from the end of that queue, so an accepted chunk is always played from its first action (`chunk.skipped == 0`, checked). Async while the median of the last 5 round trips is ≤ 850 ms (next request with 32 actions still queued), sync above it (request only when the queue is empty). Round trips: a two-state Markov chain per request, lognormal around 685 ms (normal) or 1250 ms (congested), 2–6 % failures detected after a 2.5 s timeout; realized on the held-out tables: median 712 ms, p95 1426 ms, 22.6 % over 850 ms, 2.7 % failed. The same request index gets the same round trip in both variants.
  - *hold-on-late:* an empty queue means the arm holds its pose until a reply lands.
  - *model-bridges:* an empty queue means the edge model commits a short bridge (its own open-loop rollout through the arm FK, about one expected round trip long) and a fresh request goes out planned from the bridge's end. A reply planned before the arm moved is dropped, never resumed. The bridge only moves an open, empty gripper (closing, holding and lifting wait), never changes the grip, and is capped at 4 s in a row, after which the loop holds.
- **Assist:** a simulated operator (the expert aiming at a point 2–4 cm off the cup) alone, versus with assist: inside 8 cm of the detected cup the model's command blends in over 0.6 s and keeps authority until the jaws hold the cup, then the operator lifts. A blend that hands authority back and forth stalled against the operator's offset line (0 of 54 in a smoke run), so it latches.

## Results (Nov 8, Mac mini, full run)

Dataset: 2,700 expert episodes (100 scene seeds × 9 tables × 3 variants), 2,576 gate-passing; train 1,810 episodes / 146,270 samples, val 257 / 20,783, held-out 509 / 40,988, eval-set failures 124. Training: 150 epochs, 21.8 s, best epoch 142, val loss 0.0350, held-out joint-step RMSE 0.0062 rad, grip accuracy 99.9 %. Scored on the 20 held-out seeds × 9 tables = **180 attempts per row**:

| Row | Gate passes | Wrong cup | Drops | Attempts with a collision | Other |
| --- | --- | --- | --- | --- | --- |
| Autonomous, expert | 167 / 180 | 0 | 0 | 6 | median pass 13.6 s |
| Autonomous, untrained net | 0 / 180 | 0 | 0 | 180 | |
| Autonomous, trained model | **172 / 180** | 0 | 0 | 7 | median pass 13.6 s; inference median 0.008 ms, worst p99 0.038 ms |
| Lag, hold on late | 170 / 180 | 0 | 0 | 4 | frozen mean 1.30 s (p95 3.2 s); time to hold median 9.4 s |
| Lag, model bridges | **173 / 180** | 0 | 0 | 4 | frozen mean 0.27 s (p95 1.8 s); bridged mean 1.29 s; time to hold median 8.6 s |
| Assist, operator alone | 18 / 180 | 0 | 0 | 145 | aim error 2.0–4.0 cm, median 3.0 cm |
| Assist, with assist | **162 / 180** | 0 | 0 | 5 | |

- Lag, paired by table and seed: the bridge saves 1.03 s of frozen time per attempt on average and, where both passed (170 pairs), 0.8 s median (0.96 s mean) to the start of the hold. **Most of that is the wait for the very first reply:** hold-on-late freezes 0.90 s on average before the first chunk lands, while the bridge starts moving at t = 0. After the first reply the frozen time is 0.40 s (hold) versus 0.27 s (bridge) per attempt, so bridging saves 0.13 s mid-task. Net of that first wait, the time to the hold is the same (paired mean 0.06 s, median 0). Even the undertrained `--quick` model (0 / 18 alone) passes 18 / 18 with the bridge, because the bridge only moves an open, empty gripper and the teacher plans from wherever the bridge ends. So the lag rows mostly measure the loop design, not the model's skill. Chunks played 1,335 (hold) and 1,185 (bridge), **0 actions skipped**. `skipped_actions` is measured: every accepted chunk step was either played in order or was still queued when the attempt ended. An outside trace of every teacher action also found each chunk played from action 0 in order, from exactly the state it was planned from. 22 late replies were dropped as stale, and the 4 s bridge cap was hit twice. Peak commanded tool speed 0.18 m/s (hold) and 0.19 m/s (bridge) against 0.85 m/s, so the clamp never fired in any row (the highest was 0.48 m/s, trained model alone).
- Only one failing attempt in any row grasped the target cup: lag/bridge, seed 1098, table 5. It closed on the cup at 42.5 s and the 45 s attempt ended at 6.4 cm clearance. In every failure, the gate check that blocked longest is "held by both jaws". Attempts last 30 s (150 steps) in the autonomous and assist rows and 45 s (225 steps) in the lag rows. The expert's 13 failures include 3 knocked cups; the other 10 never closed on it (unreachable or estimate-noise misses, not broken down further), and 12 of the 13 are on the five-cup tables (odd table numbers). The operator alone knocked a cup in 144 of its 162 failures. Collision columns count attempts; the bench logs one event per blocked substep, so event totals are not meaningful.
- Paired by table and seed, two of the gaps are within noise. The trained model beats the expert 172 to 167: it alone passed 6 tables and the expert alone passed 1 (exact McNemar p = 0.125). Model bridges beats hold on late 173 to 170, on 3 tables to 0 (p = 0.25). So the model is not shown to beat its teacher, and the bridge is not shown to raise the success rate. Assist versus the operator alone is a real gap: 144 tables to 0.
- Telemetry: 1,260 sessions, 160,784 events, 0 invalid in-process, and `python3 -m control_center validate` passes on all 1,260 (system Python 3.9). Decisions are by `edge`, `loop`, `safety`, `assist`, `operator` and `controller` (the schema's names; it has no `edge-model` or `bench` value, so sessions say purpose `sim` and the note says kinematic bench).
- **MuJoCo physics sim** (2 held-out seeds × 9 tables per run, 3 runs, 54 attempts in all; the cup position is the true one, with no estimate noise; a cylinder cup of radius 3.5 cm, 12 cm tall, 50 g; friction 1.0, position servos at the URDF effort limits, gravity-compensated arm links, no arm-table contact). MuJoCo's grasp_center matches the repo FK to 3.4e-7 m. With elliptic friction cones and impratio 10 the expert passes the gate on **18 / 18** (median longest hold 19.2 s, peak clearance 12.3 cm) and the bench-trained model on **17 / 18** (one below 10 cm). With MuJoCo's default pyramidal cones the grasp does not hold: **0 / 18**, the cup creeps down through the jaws to below 10 cm after about 4 s every time. So the physics result depends on contact-solver settings, and none of it is a physical result.

## Caveats

- The bench is kinematic, not physics: its evidence says `physics_simulated: false`, so nothing from it can pass the Dream exporter's positive gate. The physics rows are a MuJoCo sim with assumed mass, friction and gains, not a calibrated digital twin.
- Training data is the scripted expert's own labels only, never Dream output (see the terms note in the handoff).
- The lag teacher is the scripted expert with a modelled round trip, not Dream. Frozen time has 0.2 s resolution (the bench runs at 5 Hz).
- The policy reads a cup estimate; on the real arm that has to come from the overhead camera, which nobody detects yet.
- Nothing has run on the Orin Nano or a real arm. The PyTorch trainer has not run (no torch on this Mac).
