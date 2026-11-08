# Handoff: a small edge model on the Orin Nano

**Owner: FADI, working on the Mac Mini from this repo** (with whichever agents run there). Written Saturday night, Nov 7, from FADI's voice notes at 8:39 and 8:43 PM, the [training brief](small-model-training.md), the [Jetson brief](jetson-end-to-end.md) and the [Dream workshop takeaways](dream-workshop-takeaways.md). **Status (Nov 8, early morning): scaffold → pipeline + bench scores.** [`edge_model/`](../../edge_model/README.md) now collects, trains and scores end to end (`python -m edge_model run-all`); a policy has been trained on our own bench-expert data and scored in simulation (kinematic bench, plus a MuJoCo physics-sim grasp check). On Nov 8 it also ran on the Jetson Orin Nano's CPU, in simulation only: 0.038 ms per decision, and the MuJoCo physics bench held the cup on 9 of 9 tables ([validation](../validation/edge-model-jetson-2026-11-08.json)). It has never run on a physical arm, and no result here is a physical one.

## What changed

- **No Dream fine-tuning.** We're in the Creative track, which doesn't expect fine-tuning or training the main model. The Dream API stays the main brain, already working in the sim and on the Windows laptop. (FADI reports a physical Dream-interface test run outside this repo on Nov 7; its observation encoding and action format are not recorded here.)
- **The team's simulation doesn't change.** Its output, run through the Dream API, now trains a **small model of our own** instead of feeding a Dream fine-tune.
- **The board is a Jetson Orin Nano** with a 1 TB SSD, not the older Nano. It is also the arm server (USB to both arms and the three cameras, reachable by the whole team). Setup: [orin-nano-setup.md](orin-nano-setup.md).
- **Telemetry from the start.** Every decision, round trip and outcome is logged in one schema, so a control center can show live and historical data. The control center is built separately (its own branch and PR); its event schema is the contract this model writes to.

## The model's two jobs

1. **Bridge Dream API lag.** A Dream chunk is 48 actions (2.4 s) and the next reply lands after about 30. When a reply is late, the hybrid loop waits ([takeaways](dream-workshop-takeaways.md)); the edge model is what keeps the arm doing something sensible instead of freezing, and hands back to Dream when the reply arrives. It never skips ahead in a Dream chunk and stays under the 0.85 m/s clamp.
2. **Auto-assist.** When a person drives the arm (webcam or Quest capture), the model makes the last small moves near the cup, the way a phone tap snaps to the nearest button, so the grasp lands without the operator being exact.

Both run on the Orin Nano next to the arm, so they don't depend on the network.

## What's in `edge_model/`

| File | What it does | Tested |
| --- | --- | --- |
| `kinematics.py` | Pinned Maker Arm FK from `DreamSim/compiled_fk.py`, numpy-only IK, Unreal-to-arm-base frame conversion | In the cloud, x86 |
| `bench.py` | A **kinematic** stand-in for one cup table: seeded layouts from `DreamSim/fleet_planning.cup_layout`, a friction-proxy grasp, palm/jaw/table contacts, recorder-shaped cup evidence. Not physics. | In the cloud |
| `expert.py` | State-based scripted expert: pregrasp, 20° pitched side approach, close, 12.5 cm lift at 4 cm/s, hold. Plans from a cup *estimate*, so its misses are honest | Passed the gate on **108 of 108** bench episodes (12 seeds × 9 tables) in the cloud; on Nov 8 with 3 mm cup-estimate noise, 2,576 of 2,700 collection episodes and 167 of 180 held-out attempts |
| `gate.py` | The positive gate with the exporter's thresholds (10 cm clearance, 5 s hold, ≤15° tilt, ≤10 cm/s, both jaws, nothing else touching). Bench data is never Dream-export eligible | Via the expert run above |
| `model.py` | 19,335-parameter MLP: 14 inputs (joints, grip, tool xyz, cup offset, cup height) to a joint step and grip. Numpy inference; numpy or PyTorch trainer, one `.npz` | Numpy trainer on bench-expert data (Nov 8, Mac mini); the PyTorch trainer has not run |
| `collect.py`, `dataset.py`, `train.py` | Expert runs over seeds × 9 tables, gate-passing episodes as demonstrations, failures in a separate eval set, split by scene seed; `.npz` + manifest (`source: bench-expert`, sha256); training provenance in the checkpoint | Full run Nov 8: 2,700 episodes, 2,576 gate-passing |
| `scenarios.py`, `evaluate.py`, `telemetry_log.py` | The three scenarios below on held-out seeds, with and without the model, one control-center session per attempt | 1,260 sessions, all pass `python3 -m control_center validate` |
| `physics_bench.py` | Optional MuJoCo cup table from the same URDF and meshes (built like `Capture/contact_sim.py`), expert or trained policy | 54 physics-sim attempts (18 tables × 3 runs: expert and model with elliptic cones, expert with default cones), see results |

## Steps

1. **Record teacher data.** Two sources, kept apart in the dataset manifest: the scripted expert on the bench (fast, available tonight), and the team's Unreal fleet running Dream through the existing recorder (`Unreal/TheHumanTouch/Tools/maker_arm_demonstration_recorder.py`). Only gate-passing episodes are demonstrations; failures, including every failed Dream run, go to a separate eval set. Never relabel failed runs as demonstrations.
2. **Split by scene seed** before training, train/validation/held-out, as the [training brief](small-model-training.md) says.
3. **Train on the Mac** (PyTorch on MPS) or on the Orin (CUDA, JetPack's PyTorch wheel); the numpy trainer works anywhere. Record library, version, config, data hash and time.
4. **Score three scenarios on held-out seeds**, each with and without the model:
   - *Autonomous:* the model alone versus the expert. Success under the gate, wrong cup, drops, collisions, inference latency.
   - *Lag:* a teacher with a Dream-like round trip (median about 712 ms, late tail past 850 ms, occasional failures), hold-on-late versus model-bridges-the-gap. Time to hold, seconds frozen, peak commanded speed against 0.85 m/s.
   - *Assist:* a simulated operator with a few centimetres of aim error, alone versus with assist blending in near the cup.
5. **Write telemetry** for every attempt in the control center's schema, so each attempt can be replayed there.
6. **Deploy on the Orin Nano** behind the arm server's controller and hold, never straight to the motors, with the operator on the e-stop.

Steps 1–5 are done for the bench expert (not for Dream runs from the Unreal fleet). Step 6 is not started.

## Bench results (Nov 8)

Simulation only. Full numbers, commands and protocol: [edge-model-bench-2026-11-08.json](../validation/edge-model-bench-2026-11-08.json) and the [edge_model README](../../edge_model/README.md#results-nov-8-mac-mini-full-run). 180 attempts per row (20 held-out scene seeds × 9 tables), kinematic bench:

| Scenario | Without the model | With the model |
| --- | --- | --- |
| Autonomous (gate passes) | expert 167; untrained net 0 | trained model **172** |
| Lag (gate passes; mean seconds frozen; median time to hold) | hold on late: 170; 1.30 s; 9.4 s | model bridges: **173; 0.27 s; 8.6 s** |
| Assist, operator aiming 2–4 cm off (gate passes) | alone: 18 (145 attempts with a collision) | with assist: **162** |

- No wrong cup and no drops in any row. Every lag chunk was played from its first action (0 skipped); peak commanded tool speed in the lag rows was 0.19 m/s against the 0.85 m/s clamp, and the clamp never fired in any row (highest 0.48 m/s). Inference: 0.008 ms median per numpy forward pass on the Mac mini, 0.038 ms on the Orin's CPU ([validation](../validation/edge-model-jetson-2026-11-08.json)).
- Most of the lag rows' frozen-time gap is the wait for the very first reply. Hold on late freezes 0.90 s on average before the first chunk lands, while the bridge starts moving at t = 0. After that the frozen time is 0.40 s (hold) versus 0.27 s (bridge), and net of the first wait the time to the hold is the same. The success gaps are within noise when paired by table and seed: trained model versus expert 172 to 167 (p = 0.125), bridge versus hold 173 to 170 (p = 0.25). Assist versus operator alone (162 to 18) is a real gap.
- The lag teacher is the scripted expert with a modelled Dream-like round trip (median 712 ms, 22.6 % over 850 ms, 2.7 % failed), not Dream. The bridge only moves an open, empty gripper, drops any reply planned before the arm moved, and is capped at 4 s in a row.
- MuJoCo physics sim, 18 tables per setting, true cup position: with elliptic friction cones (impratio 10) the expert's grasp and lift held the gate's 10 cm for 5 s on 18 / 18 and the bench-trained model on 17 / 18; with MuJoCo's default friction cones it did not hold on any (0 / 18; the cup creeps down through the jaws after about 4 s). Cup mass, friction, servo gains and gravity compensation are assumptions and arm-table contact is not modelled, so this says the contact model matters, not that the real arm will hold the cup.

## What done looks like

- A trained checkpoint with provenance, scored on held-out seeds in all three scenarios, numbers stated plainly including failures.
- The same scenarios replayable in the control center from their telemetry.
- A bench result is a simulation result, not a physical one. Say which.

## Open questions

- Does the edge model drive the real arm this weekend, or only the sim? Real-arm use goes through the Orin's guards and hold.
- Which teacher data matters for the pitch: the scripted expert on the bench, or Dream runs from the Unreal fleet (whose audited cup runs so far have zero verified grasps)?
- Policy input: the bench uses a cup position estimate; on the real arm that has to come from the overhead camera. Who owns that detector?
- Does job 1 bridge *inside* a Dream chunk, or only between turns and after a Dream failure, as the router does ([hybrid model](../hybrid-model.md), `vision/hybrid.py`)? Either way the edge model never overrides a revoked grant, a stale or invalid camera frame or an arm-server trip; a late Dream chunk is not resumed after the local state has moved; and a learned "keep moving" policy never replaces the hold or the e-stop. FADI to decide.
- **Terms before training on Dream runs.** Dream's [AUP](https://platform.dreamlabs.ai/legal/aup) restricts model extraction and distillation, and its [API terms](https://platform.dreamlabs.ai/legal/api-terms) cover human proximity and local fail-safes (Nov 7 research, not legal advice). Imitating Dream's actions from the Unreal fleet (step 1) may fall under the AUP; the scripted bench expert and labels we make ourselves don't use Dream output. Independently generated labels, Dream actions as imitation targets and Dream outputs used only as context are different cases. Check the hackathon terms or ask Dream Labs before training on Dream runs. TypeSafe's [agreement](https://typesafe.ai/legal/mca) has a similar clause for Jev.
- "Jeff's contribution" in Brian's EOD notes (the movement layer, [training brief](small-model-training.md)) is probably **Jev** by TypeSafe AI: a hosted model that scores structured choices (Nov 7 research; no local checkpoint found). It could judge options offline or asynchronously, not drive the arm in real time. Confirm with Brian.
- **After the hackathon, not selected:** coordination patterns discussed on Nov 7 were Dream-with-local-bridging (above), a human co-pilot where the person stays in control and the model makes bounded corrections, and parallel residual assistance (Dream proposes, the local model adjusts small details; hardest to keep from issuing conflicting commands). Any of them needs one coordinator that serializes control authority, rejects stale commands and logs why each transition happened. Motion-assist candidates beyond this MLP: LeRobot ACT and SmolVLA, with GR00T as a heavier option. A Decision Transformer is an offline return-conditioned trajectory model, not Jev-style scoring. Liquid d1-3B is a local image/text decision-model candidate with Jetson guidance; its memory and latency under this station's camera load are unmeasured.
