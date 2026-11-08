# voice_agent: Hold My Beer voice lines

Two arm personas say short scripted lines when the handoff routine, the turn gate or the vision moments change. **Righty** (the right arm, the show-off) takes the can. **Lefty** (the left arm, which doesn't move in this demo) heckles. The voice only announces. It can't start, stop or steer the arm, and it reads nothing back from LiveKit (team decision, dashboard update 2026-11-07-1500).

Why LiveKit fits this way, and what was tested: [docs/livekit-fit.md](../docs/livekit-fit.md).

## Run it offline (any Mac, no network, no keys)

```sh
python3 -m voice_agent.render                     # 31 lines -> voice_agent/local/audio/*.wav (about 20 s)
python3 -m handoff.replay handoff/scenarios/hand_ok.json --trace | python3 -m voice_agent.play --pace
python3 -m voice_agent.play --say cold_open       # one line by id (stage fallback / manual cue)
python3 -m unittest discover -s voice_agent -t . -p 'test_*.py' -v
```

At the station, copy `handoff.run`'s event lines (stderr) to a file and follow that file from a second terminal. Run `python3 -m voice_agent.render` once first on the Mac with the speaker (a line with no WAV falls back to live `say`).

```sh
mkdir -p voice_agent/local   # tee needs the folder; render makes it on a Mac
# Mark mode, the live beat. Only stderr is copied: the g / s / h / q keys still go to this terminal.
python3 -m handoff.run --mode mark --feed operator --operator-cutoff --trials 10 \
    2> >(tee -a voice_agent/local/events.log >&2)
# Hand mode, only if it reached 8 of 10 (detector command in handoff/README.md)
vision/local/.venv/bin/python -m vision.run ... --verbose \
  | python3 -m handoff.run --mode hand --feed - --operator-cutoff --trials 10 \
      2> >(tee -a voice_agent/local/events.log >&2)
# Second terminal:
python3 -m voice_agent.play --follow voice_agent/local/events.log
```

If the routine runs on the Jetson, play the voice from a Mac that has the speaker: `ssh <jetson> tail -F <repo>/voice_agent/local/events.log | python3 -m voice_agent.play`. On Linux, `play` uses `paplay` or `aplay` for WAVs rendered on a Mac (`say` exists only on macOS).

## Add LiveKit (optional)

```sh
uv venv --python 3.12 voice_agent/local/.venv
uv pip install --python voice_agent/local/.venv/bin/python livekit livekit-api
export LIVEKIT_URL=wss://... LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...   # never in the repo
voice_agent/local/.venv/bin/python -m voice_agent.bridge --follow voice_agent/local/events.log
voice_agent/local/.venv/bin/python -m voice_agent.listen          # anywhere else, to follow along
```

The bridge plays locally like `play` and also joins room `hold-my-beer` (`LIVEKIT_ROOM`) as `hold-my-beer-voice`. It publishes every event as a JSON data message on topic `handoff`, and the line as an audio track named `hold-my-beer-voice`. The one exception is `vision.run`'s executor actions (`start_handoff`, `abort`): they read like commands, so they stay off the room. Without the env vars, or if the room can't be reached, it plays locally only. Local playback never waits for the room.

## Files

| File | What it does |
| --- | --- |
| `lines.json` | Event key → exact line → persona, plus the `say` voice per persona. Edit the text, then re-render |
| `cues.py` | Station JSON → event keys (`step:offer:1`, `end:failed:no_grasp`, `moment:can_released`, `gate:robot_turn_granted`); de-duplication and rate limit. Standard library |
| `render.py` | `say` + `afconvert` → 24 kHz mono WAVs and `manifest.json` |
| `play.py` | Reads stdin, a file or a followed file and plays with `afplay`. One line at a time, the same line not within 8 s, at most two waiting, stale ones dropped, urgent ones (stop, hot motor) first |
| `bridge.py` | `play` plus the LiveKit room (optional `livekit`, `livekit-api`) |
| `listen.py` | Subscribe-only test client: prints each data message and counts the voice audio |

## Data message (topic `handoff`, JSON)

```json
{"schema": "hold-my-beer/event/1", "seq": 14, "sent_at": 1794138201.52, "source": "handoff",
 "key": "step:offer:1", "t": 5.3,
 "event": {"t": 5.3, "kind": "step", "step": "offer", "detail": "attempt 1"},
 "line": {"id": "beer_me", "text": "Beer me.", "voice": "righty"}}
```

`source` is `handoff`, `gate`, `vision` or `trial`. `vision.run`'s executor actions (`start_handoff`, `abort`) are never published. `line` is `null` when the event stays silent. Arm `state` messages (joint angles and grip) go lossy at up to 10 Hz. Everything else is reliable. `seq` restarts when the bridge restarts.

## Limits

- Lines are written to stay off safety claims: no "safe", "clear" or "your turn" (a test enforces this). The station operator stays on the e-stop.
- `say` voices are robotic on purpose. Swapping in recorded human takes means replacing the WAVs with the same names (24 kHz mono 16-bit for the room track).
- Not tested on LiveKit Cloud, on the stage speaker, or with `handoff.run` driving the real arm.
