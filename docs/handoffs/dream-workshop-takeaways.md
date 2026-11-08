# Dream Labs workshop takeaways (Nov 8)

From the slides Dream Labs showed at the workshop, as FADI relayed them. They are the vendor's advice, not things we have measured on our rig yet. They apply to the Dream loop in the sim, the Orin Nano arm server and the [edge model](edge-model.md), and they tell the control center what to show.

| Takeaway | What to do |
| --- | --- |
| **Upload size is most of the lag** | Downscale each camera frame to **320×176** before upload. The model reads that size anyway; sending about 1 MB per request is where most of the round trip goes. |
| **Hybrid mode** | Run async while replies are on time and switch to sync when they're late. Decide on the **median of the last 5 round trips against 850 ms** (a normal round trip is about 712 ms). When a reply is late, wait for it. **Never skip ahead** in the chunk. |
| **Speed clamp** | Clamp commanded arm speed to **0.85 m/s**. A late reply once asked for 2.11 m/s and broke a gripper finger. |
| **Chunk timing** | A chunk is **48 actions (2.4 s)**. The next reply lands after about **30** of them, and the first **16** new actions are blended into what is still playing. |
| **Check before the first real run** | `python -m dream.check -- python my_loop.py`. It flags stale cameras, NaNs, grippers outside 0 to 1, and arms that never move. |
| **Camera checklist** (none of these raise an error) | Red and blue swapped; head camera upside down; the wrong view in the head role; no head camera; 4:3 squashed to 16:9. |
| **Tool center point** | Put the TCP between the fingertips, not at the wrist flange. 2 cm off moves every grasp 2 cm. |

## What the control center shows from this

- Round-trip median of the last 5, with the 850 ms line.
- The current mode (async or sync) and when it last switched, with the median that triggered it.
- Commanded speed against the 0.85 m/s limit, and every clamp.
- A "what the model saw" view: the actual 320×176 frame sent with the latest request.
- The camera checklist above, per camera.

The Dream key comes only from the `DREAM_API_KEY` environment variable. It never goes in the repo, a script or a command line.
