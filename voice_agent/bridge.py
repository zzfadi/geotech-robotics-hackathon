"""Station events into a LiveKit room: data messages plus the spoken line as audio.

  export LIVEKIT_URL=wss://<project>.livekit.cloud LIVEKIT_API_KEY=... LIVEKIT_API_SECRET=...
  python3 -m handoff.replay handoff/scenarios/hand_ok.json --trace \\
    | voice_agent/local/.venv/bin/python -m voice_agent.bridge --pace

  voice_agent/local/.venv/bin/python -m voice_agent.bridge --follow voice_agent/local/events.log

With LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET set (and the optional
`livekit` and `livekit-api` packages installed), it mints its own token, joins
room LIVEKIT_ROOM (default hold-my-beer) as `hold-my-beer-voice`, publishes
every station event (except vision.run's executor actions) as a JSON data message
on topic `handoff`, and streams each pre-rendered line on an audio track named
`hold-my-beer-voice`. Without them, or if the room cannot be reached, it plays lines locally exactly like
voice_agent.play: the stage never depends on the network.

One way only. The token grants publish and no subscribe, the bridge registers no
RPC methods and reads nothing from the room, so nothing a remote participant
sends can reach the arm, the routine or the turn gate.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import threading
import time
import wave
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple

from .cues import AUDIO, Cuer, Event, Line, Lines
from .play import LocalPlayer, add_input_args, audio_path, cue_json, open_input, records, run_local

ROOM = "hold-my-beer"
IDENTITY = "hold-my-beer-voice"
TOPIC = "handoff"
TRACK = "hold-my-beer-voice"
SCHEMA = "hold-my-beer/event/1"
STATE_MAX_HZ = 10.0
# vision.run's executor actions (start_handoff, abort) read like commands, so they never go to the
# room: it gets announcements only. They are silent in lines.json, so nothing is lost locally.
NOT_PUBLISHED = ("vision_action:",)


def _why(exc: BaseException) -> str:
    return str(exc) or type(exc).__name__


def livekit_env(env=os.environ) -> Optional[dict]:
    """The LiveKit settings, or None unless URL, key and secret are all set."""
    url, key, secret = (env.get(k, "").strip() for k in ("LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"))
    if not (url and key and secret):
        return None
    return {"url": url, "key": key, "secret": secret, "room": env.get("LIVEKIT_ROOM", "").strip() or ROOM}


def mint_token(api: Any, key: str, secret: str, room: str, identity: str = IDENTITY,
               ttl_s: int = 12 * 3600) -> str:
    grants = api.VideoGrants(room_join=True, room=room, can_publish=True, can_publish_data=True,
                             can_subscribe=False)
    return (api.AccessToken(key, secret).with_identity(identity).with_name("Hold My Beer voice")
            .with_grants(grants).with_ttl(timedelta(seconds=ttl_s)).to_jwt())


def message(ev: Event, line: Optional[Line], seq: int, now_unix: float) -> dict:
    return {"schema": SCHEMA, "seq": seq, "sent_at": round(now_unix, 3), "source": ev.source,
            "key": ev.key, "t": ev.t, "event": ev.data,
            "line": None if line is None else {"id": line.id, "text": line.text, "voice": line.voice}}


def read_pcm(path: Path) -> Tuple[int, int, bytes]:
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("%s is not 16-bit PCM" % path)
        return w.getframerate(), w.getnchannels(), w.readframes(w.getnframes())


class LiveKitLink:
    """A publish-only room connection. `rtc` is livekit.rtc (or a test fake)."""

    def __init__(self, rtc: Any, url: str, token: str, sample_rate: int = 24000, frame_ms: int = 10):
        self.rtc, self.url, self.token = rtc, url, token
        self.sample_rate, self.frame_ms = sample_rate, frame_ms
        self.room = None
        self.source = None
        self.sent = 0
        self.spoken = 0

    async def connect(self) -> None:
        rtc = self.rtc
        self.room = rtc.Room()
        await self.room.connect(self.url, self.token)
        self.source = rtc.AudioSource(self.sample_rate, 1)
        track = rtc.LocalAudioTrack.create_audio_track(TRACK, self.source)
        opts = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self.room.local_participant.publish_track(track, opts)

    async def send(self, msg: dict, reliable: bool = True) -> None:
        await self.room.local_participant.publish_data(json.dumps(msg, default=str).encode(),
                                                       reliable=reliable, topic=TOPIC)
        self.sent += 1

    async def speak(self, wav: Path) -> bool:
        rate, channels, pcm = read_pcm(wav)
        if rate != self.sample_rate or channels != 1:
            print("bridge: %s is %d Hz x%d; re-render (24 kHz mono)" % (wav.name, rate, channels),
                  file=sys.stderr)
            return False
        n = self.sample_rate * self.frame_ms // 1000
        step = n * 2
        for i in range(0, len(pcm), step):
            chunk = pcm[i:i + step]
            if len(chunk) < step:
                chunk += b"\x00" * (step - len(chunk))
            await self.source.capture_frame(self.rtc.AudioFrame(chunk, self.sample_rate, 1, n))
        await self.source.wait_for_playout()
        self.spoken += 1
        return True

    async def close(self) -> None:
        if self.room is not None:
            await self.room.disconnect()


class Bridge:
    """Events in; data messages, room audio and local playback out."""

    def __init__(self, lines: Lines, link: Optional[LiveKitLink], player: Optional[LocalPlayer],
                 audio_dir: Path = AUDIO, clock=time.monotonic, wall=time.time, out=None):
        self.lines, self.link, self.player, self.audio_dir = lines, link, player, audio_dir
        self.cuer = Cuer(lines)
        self.clock, self.wall, self.out = clock, wall, out or sys.stdout
        self.seq = 0
        self.last_state = -1e9
        self.speech: "asyncio.Queue" = asyncio.Queue()
        self.link_errors = 0

    def pending(self) -> int:
        return max(self.player.pending() if self.player else 0, self.speech.qsize())

    async def handle(self, ev: Event) -> Optional[Line]:
        line = self.cuer.cue(ev, self.clock(), self.pending())
        if line is not None:
            print(cue_json(ev, line), file=self.out, flush=True)
            if self.player:
                self.player.submit(line)
        if self.link is not None and not ev.key.startswith(NOT_PUBLISHED):
            now = self.clock()
            if ev.key == "state" and now - self.last_state < 1.0 / STATE_MAX_HZ:
                return line
            if ev.key == "state":
                self.last_state = now
            self.seq += 1
            try:
                await self.link.send(message(ev, line, self.seq, self.wall()), reliable=ev.key != "state")
            except Exception as exc:   # the room is optional; local playback carries on
                self._link_failed("publish", exc)
            if line is not None and self.link is not None:
                self.speech.put_nowait((line, self.clock()))
        return line

    def _link_failed(self, what: str, exc: Exception) -> None:
        self.link_errors += 1
        print("bridge: LiveKit %s failed (%s); continuing with local playback only" % (what, _why(exc)),
              file=sys.stderr)
        self.link = None

    async def speaker(self) -> None:
        while True:
            item = await self.speech.get()
            if item is None:
                return
            line, queued_at = item
            if self.link is None:
                continue
            if not line.urgent and self.clock() - queued_at > float(self.lines.rules["max_wait_s"]):
                continue
            wav = audio_path(self.audio_dir, line)
            if not wav.exists():
                print("bridge: no audio for %s; run python3 -m voice_agent.render" % line.id, file=sys.stderr)
                continue
            try:
                await self.link.speak(wav)
            except Exception as exc:
                self._link_failed("audio", exc)

    async def run(self, events: "asyncio.Queue") -> None:
        speaker = asyncio.ensure_future(self.speaker())
        while True:
            ev = await events.get()
            if ev is None:
                break
            await self.handle(ev)
        await self.speech.put(None)
        await speaker


def _feed_thread(loop: asyncio.AbstractEventLoop, q: "asyncio.Queue", events: Iterable[Event]) -> None:
    try:
        for ev in events:
            loop.call_soon_threadsafe(q.put_nowait, ev)
    finally:
        loop.call_soon_threadsafe(q.put_nowait, None)


async def connect_link(cfg: dict, identity: str, timeout: float = 60.0,
                       verbose_out=sys.stderr) -> Optional[LiveKitLink]:
    try:
        from livekit import api, rtc
    except ImportError:
        print("bridge: LIVEKIT_* is set but the livekit packages are not installed here; local playback only.\n"
              "  uv venv --python 3.12 voice_agent/local/.venv && "
              "uv pip install --python voice_agent/local/.venv/bin/python livekit livekit-api",
              file=verbose_out)
        return None
    token = mint_token(api, cfg["key"], cfg["secret"], cfg["room"], identity)
    link = LiveKitLink(rtc, cfg["url"], token)
    try:
        t0 = time.monotonic()
        await asyncio.wait_for(link.connect(), timeout=timeout)
    except Exception as exc:
        print("bridge: could not join %s room %r (%s); local playback only" % (cfg["url"], cfg["room"], _why(exc)),
              file=verbose_out)
        try:
            await link.close()
        except Exception:
            pass
        return None
    print("bridge: joined room %r at %s as %s in %.1f s; data topic %r, audio track %r" % (
        cfg["room"], cfg["url"], identity, time.monotonic() - t0, TOPIC, TRACK), file=verbose_out)
    return link


async def amain(a, cfg: Optional[dict]) -> int:
    lines = Lines.load(a.lines)
    player = None if a.no_local else LocalPlayer(lines, a.audio, dry_run=a.dry_run).start()
    bridge = Bridge(lines, None, player, a.audio)

    async def join() -> None:
        # Local playback never waits for the room: events speak while the bridge is still joining.
        bridge.link = await connect_link(cfg, a.identity, a.join_timeout)

    joining = asyncio.ensure_future(join())
    if a.wait_for_room:
        await joining
    q: "asyncio.Queue" = asyncio.Queue()
    loop = asyncio.get_running_loop()
    threading.Thread(target=_feed_thread, args=(loop, q, records(open_input(a), pace=a.pace)),
                     daemon=True).start()
    try:
        await bridge.run(q)
        if a.linger > 0 and bridge.link is not None:
            await asyncio.sleep(a.linger)
    finally:
        if not joining.done():
            joining.cancel()
        if bridge.link is not None:
            print("bridge: published %d data messages and %d spoken lines" % (
                bridge.link.sent, bridge.link.spoken), file=sys.stderr)
            await bridge.link.close()
        if player:
            player.close()
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_input_args(p)
    p.add_argument("--no-local", action="store_true", help="room only, no local speaker")
    p.add_argument("--identity", default=IDENTITY)
    p.add_argument("--room", default=None, help="overrides LIVEKIT_ROOM")
    p.add_argument("--linger", type=float, default=1.0, help="seconds to stay in the room after the input ends")
    p.add_argument("--join-timeout", type=float, default=60.0, help="give up on the room after this long")
    p.add_argument("--wait-for-room", action="store_true",
                   help="join before reading any input (replays and tests); by default lines play at once")
    a = p.parse_args(argv)
    cfg = livekit_env()
    if cfg and a.room:
        cfg["room"] = a.room
    if cfg is None:
        print("bridge: LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET are not all set; local playback only",
              file=sys.stderr)
        if a.no_local:
            print("bridge: --no-local with no room leaves nothing to do", file=sys.stderr)
            return 2
        lines = Lines.load(a.lines)
        player = LocalPlayer(lines, a.audio, dry_run=a.dry_run).start()
        try:
            run_local(records(open_input(a), pace=a.pace), lines, player, verbose=a.verbose)
        finally:
            player.close()
        return 0
    try:
        return asyncio.run(amain(a, cfg))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
