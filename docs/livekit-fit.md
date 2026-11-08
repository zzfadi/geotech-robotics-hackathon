# LiveKit fit for Hold My Beer

Written overnight Sunday Nov 8 (about 3:20 to 4:00 AM) for Takeshi, who has the LiveKit project, and for the standup. Nothing here ran against LiveKit Cloud. Every LiveKit result below comes from a **local** `livekit-server --dev` on FADI's Mac mini.

## Recommendation

**Use LiveKit as a one-way window onto the station, and keep the stage voice local.**

1. **Ship option (a), the scripted voice, as the stage audio.** It plays from pre-rendered files on a local speaker. No network, no keys, no speech recognition and no language model. It's built (`voice_agent/`), and every handoff event in the 20 replay scenarios maps to a line or to deliberate silence.
2. **Add option (b) on top when the team's LiveKit keys are on the station machine.** The same process joins a LiveKit room, publishes every handoff event as a data message (topic `handoff`), and streams the spoken line as an audio track. Remote teammates, Luna2, Unreal or a Quest page can then follow the run live. It's built and tested end to end on a local dev server. Remote viewers count as the sponsor integration, and the stage doesn't depend on them.
3. **Treat option (c), a camera into the room, as a stretch goal.** Use only a spare camera that no station process opens.
4. **Don't use option (d), Portal teleop, on the real arm today.** Its robot process opens the arm's CAN bus itself, which makes a second owner beside `pickup_server.py`. Its voice agent starts and stops arm motion by voice. Both break team rules. Its `portal.yaml` message format is still the right one for the Isaac → Unreal stream, which was already decided.

The voice never triggers motion. The bridge's token can publish but can't subscribe. It registers no RPC methods and never reads room data. A test checks that `voice_agent/` contains no arm client, gate call or RPC handler.

## What LiveKit offers here (public sources, read Sunday 3:30 AM)

- **Hackathon starter** ([livekit.info/pah](https://www.livekit.info/pah)): sign up for LiveKit Cloud, credits with code `physical-ai-hackathon`, install the `lk` CLI. The voice-agent quick start is `lk app create` from the agent starter, then `uv sync` and `console`. It also links the Portal robotics example (`main`, `dc/voice`, `star-leader` branches) and docs on turn detection, noise cancellation and wakewords.
- **Portal, `dc/maker_arm` branch** ([livekit-examples/portal-makermods](https://github.com/livekit-examples/portal-makermods/tree/dc/maker_arm)): human-in-the-loop learning with LiveKit Portal. The runnable peers are `robot`, `teleoperator`, `recorder`, `dream-1`, `dream-1-tasks`, `act`, `hand-teleop`, `voice-agent` and `task-monitor`, all sharing `portal.yaml`.
  - Each frame carries three H.264 camera tracks (overhead, left wrist, right wrist) and 14 float32 positions, 6 joints in radians plus the gripper per arm, at 30 Hz. State and actions are lossy; control messages are reliable.
  - Maker Arm v1 support: `MAKERMODS_MODEL=makers` on every peer selects the RobStride driver, its limits and an 80 mm gripper scale. macOS uses candleLight USB-CAN (`gs_usb:<serial>`). The arm is zeroed at its folded pose in MakerMods Lab, and the gripper is measured open and closed.
  - Needs Python 3.12, `uv`, a LiveKit server, a Dream Labs key for Dream-1, CUDA for ACT training and a GPU for the task monitor.
  - `uv run robot` **energizes the arms and owns their CAN bus**.
- **Portal's voice agent** (`src/voice_agent.py`, tutorial chapter 10): LiveKit Agents 1.8, with speech-to-text, the language model and text-to-speech all on **LiveKit Inference**. The defaults are AssemblyAI `universal-3-6-pro`, `google/gemma-4-31b-it` and Fish Audio `s2.1-pro`, plus LiveKit's turn detector and ai-coustics noise cancellation.
  - **A LiveKit Cloud key and secret are the only credentials it needs**, with no OpenAI, Deepgram or ElevenLabs keys. With a self-hosted server it adds `LIVEKIT_INFERENCE_API_KEY/SECRET` from a Cloud project.
  - It drives Dream-1 through RPCs: `dream.resume` hands the arms to Dream-1, `dream.pause`, and the robot's `home` and `park`. Its own tutorial says a spoken stop lands "a second or two" late.
  - Inference use is billed to the Cloud project. Costs weren't checked.

## The options

| Option | Needs | Fits the MVP? | Built and tested here |
| --- | --- | --- | --- |
| **(a) Scripted voice**: fixed lines per handoff/gate event, local speaker | macOS `say` and `afplay` (Linux: `paplay`/`aplay` with WAVs rendered on a Mac). No network, no keys | **Yes.** No model output, no network, and it can't touch the arm | Built: `voice_agent/lines.json`, `render.py`, `play.py`. 31 lines rendered (46 s of audio). Cue sequences checked on paced replays. `afplay` exit 0 at volume 0. **Not heard on a stage speaker** (it was 3 AM) |
| **(b) Events and voice into a LiveKit room**: data messages on topic `handoff`, voice audio track | The team's `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` as env vars. `livekit` and `livekit-api` Python packages in a uv venv | **Yes, as an add-on.** Publish-only, and local playback never waits for it | Built: `voice_agent/bridge.py`, `listen.py`. **Tested end to end on a local dev server only** (below) |
| **(c) Station camera into the room** | A process that publishes a camera track (Portal's robot peer, a LiveKit SDK publisher, or a browser on a spare laptop) plus a token | **Stretch.** The Orin's detector camera belongs to `vision.run`; a second opener would fight it. Use a separate webcam or the laptop's own | Not built |
| **(d) Portal teleop / Dream-1 on the real arm** | Python 3.12 Portal stack, `MAKERMODS_MODEL=makers`, candleLight CAN, gripper calibration, leader arms or webcam hand teleop, Dream key | **No.** It's a second arm and CAN owner, and voice-driven motion is against the team decision. Keep `portal.yaml` as the message format for Isaac → Unreal | Read only |
| Live improv voice (Agents STT → LLM → TTS) | The Cloud key only (LiveKit Inference). A mic, push-to-talk on stage | **Not before 3 PM.** Model output is live on stage, Inference billing hasn't been checked, and nothing here exercised it. If Brian wants it, keep it off the arm's path and use it only for off-script replies | Not built |

## The lines

`voice_agent/lines.json` maps each event to an exact line and a persona: **Righty** (the right arm, the show-off who takes the can) and **Lefty** (the left arm, which doesn't move and only heckles). A hand run sounds like this:

> Lefty: "Oh no. He's got that look." · Righty: "Say no more. Watch this." · "Beer me." · *(grasp)* "Say less." · *(they let go)* "Thank you kindly." · *(held at carry)* "Hold my beer? Held." · Lefty: "Show-off."

Failures get their own lines: "Uh... little help?" when a grasp misses and it offers again, "Fine. Keep it." when the person never lets go, "Hey! That was mine." when the can is pulled back, "And... scene." on an operator stop, "He's overheating. Again." on a hot motor. A hand in the path gets Lefty's "Oh! Hi there."

The lines describe what the arm does and never tell the person what's safe. The dashboard's earlier examples "your turn" and "I'll wait" are deliberately not used, since they read as a promise that the arm won't move. A test bans words like *safe*, *clear*, *your turn* and *reach*. Edit the text in `lines.json`, then re-render.

## What was tested here (exact)

All on FADI's Mac mini, no hardware, no Cloud.

| Check | Result |
| --- | --- |
| `python3 -m unittest discover -s voice_agent -t . -p 'test_*.py'` (Python 3.9.6, and again in the 3.12.15 venv) | 30 tests OK. Every step, stop reason and contact in `handoff/routine.py`, every gate state in `turn_gate/gate.py` and every vision moment (including the transition moments `hand_withdrawn`, `offer_withdrawn`, `hand_returned` and `observation_recovered`, which the first draft missed) resolves to a line or deliberate silence. The fake LiveKit link publishes the right topic, JSON and audio frames, and a failed room falls back to local playback |
| Every replay scenario through the cue mapping (the 20 in the working tree at 3:50 AM, including another agent's new `release_not_seen` stop) | No unmapped event. `handoff.replay`: 20 of 20 passed |
| `python3 -m voice_agent.render` | 31 WAVs, 24 kHz mono 16-bit, 46 s total; Righty is `Rocko (English (US))`, Lefty is `Samantha (English (US))` |
| Paced dry runs (`handoff.replay ... --trace \| voice_agent.play --dry-run --pace`) | hand_ok: 7 lines, as quoted above. Path blocked and resumed adds "Oh! Hi there." and "And we're back." Empty close adds "Uh... little help?" Pulled back: "Hey! That was mine.", "Back to his corner.", "Well. That happened." |
| **LOCAL end to end**: `livekit-server` 1.13.11 `--dev` with a random local key (not the `devkey` placeholder), signalling on 127.0.0.1:7880 | `listen` (subscribe-only) joined, then `bridge --pace --wait-for-room` replayed the hand_ok trace. The bridge published 35 data messages and 7 spoken lines (10.8 s of speech). **The listener received 35 of 35, in order (seq 1–35)**, 7 of them carrying a line, subscribed to the `hold-my-beer-voice` track and counted 1,743 audio frames (17.4 s). The server was stopped afterwards |
| **Independent re-run (LOCAL dev server, about 3:55 AM)**: same `livekit-server` 1.13.11, a fresh random key in a temp file (deleted afterwards), the hand_ok trace plus one vision frame carrying `start_handoff` and `abort` actions | Bridge published 36 data messages and 7 spoken lines. The listener received 36 of 36, 7 with a line, from publisher `hold-my-beer-voice` only, `"voice_track": true`, 2,049 audio frames (20.5 s). The vision moment arrived; neither executor action did. Joins took 30 s (listener) and 49 s (bridge). Server stopped, no process left |
| Fallbacks seen on the way | Wrong key: 401 Unauthorized, so the bridge plays locally only. Join timeout: local only. With the room still joining, lines play at once (the join runs in the background unless `--wait-for-room`) |
| Join time on this Mac | Slow: the listener took 47 s in an earlier run and 30 s in the independent re-run, the bridge 49 s in both, and some earlier attempts timed out (`wait_pc_connection timed out`). This Mac's firewall is on in stealth mode and the Homebrew `livekit-server` isn't allowed through it. Only an IPv6 local host pair connected. The firewall was left alone. A subscribe-only peer joined the local server only in single-peer-connection mode, so `listen` uses that by default; `--dual-pc` switches back if Cloud refuses it. **Expected to be fast on LiveKit Cloud, not verified** |

Not tested: LiveKit Cloud; audible playback on the stage speaker; `handoff.run` with the real detector or arm feeding the voice; any Unreal, Quest or browser consumer of the data messages; the `--print-token` viewer path in a browser.

## Morning setup for Takeshi (about 10 minutes)

On the machine with the stage speaker. That's a Mac for `say`; if the routine runs on the Jetson, follow its log over SSH (step 6).

```sh
cd geotech-robotics-hackathon && git pull --ff-only origin main

# 1. Offline voice first (works with no network)
python3 -m voice_agent.render
python3 -m handoff.replay handoff/scenarios/hand_ok.json --trace | python3 -m voice_agent.play --pace   # listen

# 2. LiveKit packages, isolated (git-ignored)
uv venv --python 3.12 voice_agent/local/.venv
uv pip install --python voice_agent/local/.venv/bin/python livekit livekit-api

# 3. Credentials from the LiveKit Cloud project "The Human Touch" (Settings > API keys; make a key
#    for this station). Env vars only, never in the repo or in Slack.
export LIVEKIT_URL=wss://<project>.livekit.cloud LIVEKIT_API_KEY=<key> LIVEKIT_ROOM=hold-my-beer
read -rs LIVEKIT_API_SECRET && export LIVEKIT_API_SECRET

# 4. Smoke test: terminal A listens, terminal B replays a handoff into the room
voice_agent/local/.venv/bin/python -m voice_agent.listen --max-seconds 120
python3 -m handoff.replay handoff/scenarios/hand_ok.json --trace \
  | voice_agent/local/.venv/bin/python -m voice_agent.bridge --pace --wait-for-room
```

**Pass:** the bridge prints `joined room 'hold-my-beer'` and then `published N data messages and 7 spoken lines`. The listener's summary shows the same N `messages` (35 on Sunday's tree), `"voice_track": true` and a non-zero `audio_seconds`. On a failure, the bridge prints why and keeps playing locally.

5. **Hear the room from a browser (optional, not tried here):** `voice_agent/local/.venv/bin/python -m voice_agent.listen --print-token` prints a 12-hour subscribe-only token. Paste it with the `wss://` URL into a LiveKit custom-connection viewer such as LiveKit Meet's custom tab, or use any LiveKit client.

6. **At the station:** copy `handoff.run`'s event lines to a file and run the bridge on it:

   ```sh
   mkdir -p voice_agent/local   # tee needs the folder (render makes it on a Mac)
   python3 -m vision.run ... --verbose \
     | python3 -m handoff.run --mode hand --feed - --operator-cutoff --trials 10 \
         2> >(tee -a voice_agent/local/events.log >&2)
   voice_agent/local/.venv/bin/python -m voice_agent.bridge --follow voice_agent/local/events.log
   # routine on the Jetson, speaker on a Mac:
   ssh <jetson> tail -F <repo>/voice_agent/local/events.log | voice_agent/local/.venv/bin/python -m voice_agent.bridge
   ```

   With no credentials, `python3 -m voice_agent.play --follow voice_agent/local/events.log` is the same voice with no room. `python3 -m voice_agent.play --say bow` plays one line by hand (also `cold_open`, `bow_reply`, `mine_now`, `up_we_go`).

## For anyone consuming the room

Topic `handoff`, one JSON object per message. Schema, fields and an example are in [voice_agent/README.md](../voice_agent/README.md#data-message-topic-handoff-json). Read `key`, `line` and `event`; arm `state` arrives lossy at up to 10 Hz. Nothing published there is a safety signal or a hold: the station's own guards and the operator on the e-stop are the only stop. Never send commands back. `vision.run`'s executor actions (`start_handoff`, `abort`) are never published, because they read like commands. The bridge doesn't listen, and nothing in the room is wired to the arm.
