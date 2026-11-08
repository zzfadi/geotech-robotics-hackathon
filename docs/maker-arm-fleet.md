# Parallel Maker Arm trials

The Luna2 [Dream panel](https://luna2.tail6864ac.ts.net/) controls **nine stations total in a 3×3 grid**. White tabletop stickers show **1–9**. Table 1 is the original station (internal T00); tables 2–9 are T01–T08. The two extra rooms have been removed, with matching Mac/Luna2 scene checks.

The active demo goal is [Hold My Beer](hold-my-beer.md): drinking cups only. The prior block trials below are historical. Cup conversion and hold validation are in progress.

## Commands and layouts

Select one or more test tables, then choose a shared prompt, each table's configured prompt, or generated tasks. The main prompt also accepts explicit targets, such as `Table 3: hold my beer cup` or `All tables: lift the drinking cup 10 cm and hold it upright`. Its target preview shows the destination before submission. “All tables” addresses all nine stations, including Table 1, through the fleet worker. Invalid or conflicting addresses are rejected. The generated-task review lists the prompt and target for each selected table. A seed reproduces the same tasks and layouts; selecting a subset preserves each table's plan for that seed. Planning alone does not move Unreal or call Dream.

Object randomization repositions one empty drinking cup per table within a small set of left-arm workspace slots. The default `fleet_cups.json` uses a controlled open pregrasp pose, so its first lift test does not establish approach learning. Candidate positions remain within the tabletop. The runtime sets height from actual bounds and resets both arms before a randomized trial. This is approximate scene preparation, not a guarantee that every target is reachable or graspable.

Each station has its own Dream session and camera/state feedback loop in one authoritative Unreal simulation world. The unified fleet includes the original station; historical main-worker records remain accessible, and simultaneous legacy-worker motion is excluded. All selected sessions must be ready before a batch starts; unavailable stations are not silently omitted.

## Evidence and limits

Generated lift tasks have a measured objective: the selected prop remains at least 10 cm above its initial height for five continuous seconds, within 15 degrees of upright and at no more than 10 cm/s, with physics and gravity enabled and no attachment. Recorded video still needs review. Arbitrary free-text tasks without a measurable objective report **unverified**, even when their control loop completes. Errors and stopped trials remain distinct from measured failure or success.

Per-table files under ignored `Saved/DreamFleet/<run>/Txx/` retain raw/adapted actions, request timestamps, playback rows, joint changes, observations and camera clips. The batch summary measures overlap of outstanding client requests; this does not prove the provider executed those requests simultaneously. Nine active simulation loops share four API request slots with bounded capacity retries. Each queued request refreshes its camera/state observation before inference. A project-scoped worker lock and server-side worker lease prevent duplicate workers from claiming the same batch.

New fleet recordings capture overhead views during motion, inference waits and held poses, preserving elapsed capture spacing; wrist clips are sampled policy observations rather than continuous wrist video. The panel shows only recordings actually uploaded. Opt-in idle previews use nine independent persistent camera histories with a staggered total rate of five captures per second, refreshing each station about every two seconds. The worker stops idle capture during simulation and publishes runtime frames instead. Actual capture timestamps drive freshness labels; repeatedly uploading an old JPEG does not make it live. All nine browser feeds, including Table 1, were visually verified fresh before the nine-table batch. Earlier ten-table results below are historical.

## Validation status

The nine-room scene matches on Mac and Luna2: exact 3×3 table/floor centers, stickers 1–9, 27 numbered wall/floor decals and no extra T09/T10 actors. See [scene parity](validation/nine-room-parity-2026-11-07.json). Unified nine-table control and live previews are deployed. Pickup testing remains in progress.

The controlled two-table probe `824ea8168f4e4a4d8c9272cb81b983c1` used open pregrasp poses. Red measured a 15.08 cm lift held 2.68 seconds after three Flash calls; green failed after 24 calls. The conservative verifier reports the red result as unverified until independent grasp review. See [contact probe](validation/fleet-contact-probe-2026-11-07.json). Height alone can be caused by other support, so it is not sufficient to declare a grasp. The green policy proposed a below-table trajectory; the next runtime rejects pad geometry entering the tabletop rather than projecting or teleporting it.

The initial ten-table trial on November 7 (`d0118f1f10614513af8540cf08ab9372`) completed with five provider `NO_CAPACITY` failures and four small joint-limit overshoots. T02 measured an 11.94 cm lift held for 2.51 seconds. That first batch does not establish ten successful executions. The subsequent browser-launched randomized batch `c45502194a2746a4a41bea810a8a72be` (seed 1453311647) ran all ten simulations together for 170.44 seconds after all ten live camera views were visually verified. All ten received real Dream actions. There were 84 attempts: 49 returned actions, 34 session renewals and one recovered capacity refusal, with at most four outstanding requests. Observation age at request start was at most 1.639 seconds.

Seven stations (T02, T04–T06, T08–T10) moved; T01, T03 and T07 rejected policy orientation references outside the allowed tolerance before motion. T02 completed ten cycles and measured a peak lift of 4.17 cm. Six other moving trials reached the bounded simulation deadline. **No station passed the 10 cm held-lift objective in this randomized batch.** All ten overhead recordings were uploaded; three local camera clips per station were produced. See [per-table outcomes](validation/fleet-parallel-trial-2026-11-07.json) and [Multi-User measurements](validation/fleet-multiuser-proof-2026-11-07.json). Luna2 received intermediate movement for the seven moving stations and restored all 260 sampled actors afterward.

The visual CAD frames remain official MakerMods geometry. Contact shapes, actuator behavior and physical properties are approximate; these trials do not establish a calibrated hardware twin.
