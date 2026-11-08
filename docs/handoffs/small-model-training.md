# Handoff: train a small model from our simulation

**Owner: FADI, working on the Mac Mini from this repo** (with whichever agents run there). Written Saturday night, Nov 7, from [the team plan](../plan.md), [Dream fine-tuning readiness](../dream-finetuning.md), [the demonstration recorder](../dream-demonstration-recorder.md), [parallel Maker Arm trials](../maker-arm-fleet.md) and the EOD sync. No training has been run.

> **Update, Nov 7, 8:45 PM (FADI):** no Dream fine-tuning; we're in the Creative track. The small model is now an **edge model on the Jetson Orin Nano** that bridges Dream API lag and does auto-assist. Its brief and the first scaffold are in [edge-model.md](edge-model.md); Dream's own advice is in [the workshop takeaways](dream-workshop-takeaways.md). Step 6 below (Dream fine-tuning) is dropped. The rest of this brief still describes the data and the gate.

## Goal

Train a small arm-control model of our own on data from the simulation Brian and Denys built (nine parallel Unreal tables, Isaac physics), and score it with a repeatable eval harness. It should be small enough to run next to the arm (the Mac, or later the Jetson; see [the Jetson brief](jetson-end-to-end.md)).

The EOD sync points at two uses: the "custom model for the final arm-to-human movement layer" (smooth, intent-aware movement rather than copying the hand), and "simulation as a data pipeline" (benchmark models against simulated human interactions). Long term the team may move arm control to open weights.

## Why it matters for Sunday

It turns the pitch line "simulation is a data pipeline" into a result: data collected in the parallel sims, a model trained on it, and a scored before/after. That is the other track's kind of work (fine-tuning, eval harness, scored attempts). It is **not** part of the MVP. Time-box it: if there is no scored result by late morning, the pitch shows the pipeline and the eval harness, not a trained model.

## Inputs and where they live

| Input | Where |
| --- | --- |
| Parallel cup-table fleet, seeded layouts, worker and verification | `DreamSim/` (`fleet_worker.py`, `fleet_planning.py`, `pickup_verification.py`, `cup_metrics.py`), `docs/maker-arm-fleet.md` |
| Synchronized demonstration recorder (5 Hz, three cameras, measured state, acknowledged actions) | `Unreal/TheHumanTouch/Tools/maker_arm_demonstration_recorder.py`, `docs/dream-demonstration-recorder.md` |
| Exporter to a LeRobot v2.1 dataset, 32-D joint+EEF state/action, local validation | `DreamSim/demonstration_dataset.py` |
| Positive-episode gate (10 cm clearance, 5 s hold, tilt ≤ 15°, no attachment, named review) | `docs/dream-demonstration-recorder.md` |
| Audit of existing runs: 23 table episodes, **zero verified successful grasps** | `docs/dream-finetuning-readiness.json` |
| Scripted mechanical cup hold that worked in sim (not a policy, not a dataset) | `docs/dream-finetuning.md` |
| Real-arm pickup waypoints from Saturday | `arm/evidence/2026-11-07-cylinder-pickup.json` |
| Human capture into the sim (webcam, Quest) | `Capture/README.md`, `docs/performance-capture.md` |
| Isaac scene parity and the pose stream | `Capture/isaac/README.md` |

The Unreal project and the fleet run on Luna2 and Denys's Mac; check with Denys before starting a recording run on the shared session (one editor per machine, see `AGENTS.md`).

## Steps

1. **Pick one narrow behavior.** Recommended: reach-and-grasp of a cup handed or placed in the workspace (the "hold my beer" approach), single arm, joint+EEF actions as the exporter defines them. Write the choice here.
2. **Collect expert demonstrations in sim.** Use a scripted expert (the mechanical cup-hold controller or the IK planner) as the demonstrator, driven through the recorder across the nine tables with different seeds. Only episodes that pass the positive gate count; failures go to a separate eval set. Never relabel the old failed Dream runs as demonstrations.
3. **Export and validate.** Run `DreamSim/demonstration_dataset.py` per episode and check the result locally. Split train/validation by scene seed before conversion.
4. **Train locally on the Mac.** A small imitation policy (for example a LeRobot ACT or diffusion policy, or a plain behavior-cloning MLP on state if images are too slow) using PyTorch on Apple Silicon. Record the library, version, config, data hash and training time.
5. **Eval harness.** Run the trained policy on held-out seeds in the fleet and report, per attempt: success under the positive gate, wrong-cup selections, drops, collisions and latency. Compare against the scripted expert and against Dream-1 on the same seeds.
6. **Optional, later:** the same dataset can go to Dream fine-tuning, but that is a paid job (the official tutorial lists about $100 per 500 steps and 7–8 hours for 1,000 steps) and cannot be cancelled once queued. It needs FADI's explicit yes and a price cap first. Not for Sunday.
7. **Report.** A dashboard update in `dashboard/updates/` (workstream `dream1`) with the scored table and where the dataset and checkpoint live. Keep datasets and checkpoints out of Git.

## What done looks like

- At least a handful of gate-passing sim demonstrations across several seeds, exported and validated.
- A trained checkpoint with its config and data provenance recorded.
- A scored eval on held-out seeds, side by side with the scripted expert and Dream-1.
- The numbers stated plainly, including failures. A simulation success is not a physical result.

## Open questions

- Which behavior does the team want the model for: the reach-and-grasp, or the "assisted intent" smoothing between the human hand and the arm?
- Is "Jeff's contribution" (named in the EOD notes for the movement layer) a model we should train against or adopt?
- Which recorder rate: 5 Hz works today; Dream's format also accepts 15 or 30 Hz.
- Does the trained model ever drive the physical arm this weekend? If so, it goes through the Jetson controller's guards and hold, operator on the e-stop.

## What needs the Mac or hardware

- The Mac Mini for training (Apple Silicon GPU) and for holding datasets and checkpoints.
- Access to the Unreal fleet on Luna2 or Denys's Mac to record demonstrations, coordinated with Denys.
- No paid API use without FADI's approval. The Dream key stays in the password manager as `DREAM_API_KEY`, never in the repo.
