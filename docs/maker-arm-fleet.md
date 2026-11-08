# Parallel Maker Arm trials

The Luna2 [Dream panel](https://luna2.tail6864ac.ts.net/) controls the original station and ten test stations. White tabletop stickers show **1–11**: original station 1, test stations 2–11. Internal trial keys remain T00 and T01–T10.

## Commands and layouts

Select one or more test tables, then choose a shared prompt, each table's configured prompt, or generated tasks. The main prompt also accepts explicit targets, such as `Table 3: pick up the red cube` or `All tables: lift the red cube 10 cm`. Its target preview shows the destination before submission. “All tables” addresses the ten test stations (stickers 2–11); Table 1 retains its original worker. Invalid or conflicting addresses are rejected. The generated-task review lists the prompt and target for each selected table. A seed reproduces the same tasks and layouts; selecting a subset preserves each table's plan for that seed. Planning alone does not move Unreal or call Dream.

Object randomization rearranges five supported props: red block, green cube, orange cube, orange block with a yellow rod, and orange cylinder. Candidate positions remain within the tabletop and separate the conservative prop footprints. The runtime sets height from actual bounds and resets both arms before a randomized trial. This is approximate scene preparation, not a guarantee that every target is reachable or graspable.

Each test station has its own Dream session and camera/state feedback loop in one authoritative Unreal simulation world. The main-station worker and fleet worker are mutually exclusive. All selected sessions must be ready before a batch starts; unavailable stations are not silently omitted.

## Evidence and limits

Generated lift tasks have a measured objective: the selected prop remains at least 10 cm above its initial height for two seconds, with physics and gravity enabled and no attachment. Recorded video still needs review. Arbitrary free-text tasks without a measurable objective report **unverified**, even when their control loop completes. Errors and stopped trials remain distinct from measured failure or success.

Per-table files under ignored `Saved/DreamFleet/<run>/Txx/` retain raw/adapted actions, request timestamps, playback rows, joint changes, observations and camera clips. The batch summary measures overlap of outstanding client requests; this does not prove the provider executed those requests simultaneously. Ten active simulation loops share four API request slots with bounded capacity retries. Each queued request refreshes its camera/state observation before inference. A project-scoped worker lock and server-side worker lease prevent duplicate workers from claiming the same batch.

Fleet recordings show overhead motion; wrist clips are sampled policy observations rather than continuous wrist video. The panel shows only recordings actually uploaded. Opt-in idle previews use ten independent persistent camera histories with a staggered total rate of five captures per second, refreshing each station about every two seconds. The worker stops idle capture during simulation and publishes runtime frames instead. Actual capture timestamps drive freshness labels; repeatedly uploading an old JPEG does not make it live. All ten browser previews were visually verified before the integrated parallel test.

## Validation status

The initial ten-table trial on November 7 (`d0118f1f10614513af8540cf08ab9372`) completed with five provider `NO_CAPACITY` failures and four small joint-limit overshoots. T02 measured an 11.94 cm lift held for 2.51 seconds. That first batch does not establish ten successful executions. The subsequent browser-launched randomized batch `c45502194a2746a4a41bea810a8a72be` (seed 1453311647) ran all ten simulations together for 170.44 seconds after all ten live camera views were visually verified. All ten received real Dream actions. There were 84 attempts: 49 returned actions, 34 session renewals and one recovered capacity refusal, with at most four outstanding requests. Observation age at request start was at most 1.639 seconds.

Seven stations (T02, T04–T06, T08–T10) moved; T01, T03 and T07 rejected policy orientation references outside the allowed tolerance before motion. T02 completed ten cycles and measured a peak lift of 4.17 cm. Six other moving trials reached the bounded simulation deadline. **No station passed the 10 cm held-lift objective in this randomized batch.** All ten overhead recordings were uploaded; three local camera clips per station were produced. See [per-table outcomes](validation/fleet-parallel-trial-2026-11-07.json) and [Multi-User measurements](validation/fleet-multiuser-proof-2026-11-07.json). Luna2 received intermediate movement for the seven moving stations and restored all 260 sampled actors afterward.

The visual CAD frames remain official MakerMods geometry. Contact shapes, actuator behavior and physical properties are approximate; these trials do not establish a calibrated hardware twin.
