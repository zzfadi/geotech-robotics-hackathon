# Creative track · judging strategy

The organizer’s **Creative track rubric v1** uses five 1–5 ratings. Points on each axis equal its weight × rating ÷ 5. The station format is a robot demo under three minutes, followed by two minutes of questions. The top five present for five minutes. Aim for a first-attempt live run. A video-only demo caps Demo quality at 3/5, which caps the total at **92/100** even with four other perfect ratings. The deck cannot establish a physical reliability rate.

| Axis | Weight | What a judge would need to see for a 5 | Evidence and the next decision |
| --- | ---: | --- | --- |
| Originality | 25 | An interaction the room has not seen before. | Lead with the social cue: a human changes what they are doing, and the robot takes a different, complementary action. A can pickup alone is familiar. If feature-locked, have the person reclaim the can at a different time and let the judge see the response. Emmy illustrates the idea; she is not hardware evidence. |
| Technical difficulty | 20 | A long-horizon task, a tool, deformable manipulation or coordinated bimanual work. | The documented single right-arm replay is a baseline. The strongest shared-station extension is a continuous human → arm A → arm B → human exchange, with visible transfer of possession. Attempt it on stage only after the whole chain passes rehearsal. Two disconnected movements do not demonstrate coordination. Disclose any additional camera or headset; the rubric permits a discount for equipment other teams lacked. |
| Robustness | 25 | The locked routine works almost every time. | Keep a run ledger on identical hardware: start pose, placement, result, manual intervention and reset time. Include misses. Try both the nominal mark and one declared variation. Report successes over total trials with those conditions only. A simulated hold is separate evidence. |
| Demo quality | 20 | Clear first-attempt live operation in less than three minutes. | Start parked on a marked station; operator confirms readiness. Let the audience see the can, empty gripper, movement and final state. The script budgets 2:52 including a 30 s live beat. Run three uninterrupted dress rehearsals with a stopwatch. If time runs long, trim gallery narration before trimming the physical run. |
| Presentation | 10 | Explain the problem, result, a failure and the next experiment. | State what a robot maker gains from remote human rehearsal. Show the separate simulation and real-arm observations. Give a precise failure and a testable next step. End on the project QR. |

## Station rehearsal card

1. Freeze one routine. Record software revision, prop, starting marks, camera framing and operator. Test any optional handshake, fist bump, second arm or voice beat separately.
2. Run ten consecutive trials without omitting failures. Record pass/fail, interventions, elapsed time and whether the robot returned to its parked pose. Define success before the first attempt. Ten trials are an internal rehearsal target; the judges specify no numeric threshold.
3. Run the actual presentation three times under three minutes. Stage the initial can position before the judge arrives. Keep the physical view unobstructed. If an unproven handoff, voice reply or greeting costs first-run reliability, use the verified table pickup and identify the control method.
4. Save one uncut physical run for fallback. If playback is necessary, say it is recorded. During Q&A show the ledger and explain the biggest remaining gap. A failed live attempt stays in the record.
5. If reliability earns 4 rather than 5, that alone costs five points. If technical difficulty earns 3 rather than 5, that costs eight points. At 5/3/4/5/5 the weighted total is 87. This is a planning scenario, not a forecast.

## Suggested Q&A

**What did the robot actually learn?** The station result was a scripted right-arm waypoint sequence. Dream supplied simulated actions. Human feedback, learning a complementary policy and transferring it to hardware are the product direction. Demonstrate only the parts verified at freeze.

**What failed?** In the documented friction-grip simulation batch 26, one table held the cup below the assigned lift height. That was a failed task despite the stable hold. This is a simulated failure under a particular model, not a physical-arm reliability measurement.

**Who uses this?** A robotics team brings a robot model and an interaction, invites remote people into repetitions and reviews what those people judged helpful before testing the behavior on hardware.

Sources: [organizer rubric attachment](https://geotech-org.slack.com/archives/C0CFR1X1W2E/p1794088346753569), [Slack cold-open draft](https://geotech-org.slack.com/archives/C0CFR1X1W2E/p1794091576268479), [later scripted voice beats](https://geotech-org.slack.com/archives/C0CFR1X1W2E/p1794092429568219), opening discussion in the Granola note supplied in chat (4:39–6:57, 10:36–11:01 and 13:41–14:29), [physical baseline](../../../arm/README.md), [simulation trial log](../../../docs/validation/claude-cup-iterations-2026-11-07.md). Source PDF fingerprint: c160899d8a5a39b958b34f5401975050492f2627e8d93eedbaa0297394f46729.

Use the [blank station ledger](station-trials.csv) for actual observations. Score each axis separately after a dress run and calculate 5 × Originality + 4 × Technical difficulty + 5 × Robustness + 4 × Demo quality + 2 × Presentation. Do not fill missing evidence with a projected score.
