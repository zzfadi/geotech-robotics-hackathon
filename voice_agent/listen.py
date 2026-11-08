"""Follow the Hold My Beer room from anywhere: print each data message, count voice audio.

  voice_agent/local/.venv/bin/python -m voice_agent.listen                  # until Ctrl-C
  voice_agent/local/.venv/bin/python -m voice_agent.listen --expect 20 --max-seconds 60

Needs LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET (a viewer token is
minted locally: subscribe only, no publish). Prints one JSON line per message on
topic `handoff`, then a summary line. This is the reference for anything else
that wants to follow the station live (a dashboard, Unreal, a Quest page): read
`key`, `line` and `event` from each message; never send anything back.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import timedelta

from .bridge import ROOM, TOPIC, TRACK, livekit_env


def viewer_token(api, cfg: dict, identity: str, hours: float = 12) -> str:
    """Subscribe-only: can watch and listen, cannot publish media or data."""
    grants = api.VideoGrants(room_join=True, room=cfg["room"], can_publish=False, can_publish_data=False,
                             can_subscribe=True)
    return (api.AccessToken(cfg["key"], cfg["secret"]).with_identity(identity).with_name("Hold My Beer listener")
            .with_grants(grants).with_ttl(timedelta(hours=hours)).to_jwt())


async def listen(cfg: dict, identity: str, expect: int, max_seconds: float, quiet: bool,
                 single_pc: bool = True) -> dict:
    from livekit import api, rtc

    token = viewer_token(api, cfg, identity)
    room = rtc.Room()
    got = {"messages": 0, "lines": 0, "keys": [], "audio_frames": 0, "audio_seconds": 0.0,
           "voice_track": False, "publishers": set()}
    done = asyncio.Event()
    tasks = []

    @room.on("data_received")
    def _on_data(packet) -> None:
        if packet.topic != TOPIC:
            return
        try:
            msg = json.loads(bytes(packet.data).decode())
        except ValueError:
            return
        got["messages"] += 1
        got["keys"].append(msg.get("key"))
        if msg.get("line"):
            got["lines"] += 1
        if packet.participant is not None:
            got["publishers"].add(packet.participant.identity)
        if not quiet:
            print(json.dumps({"from": packet.participant.identity if packet.participant else None,
                              "seq": msg.get("seq"), "key": msg.get("key"), "line": msg.get("line")}),
                  flush=True)
        if expect and got["messages"] >= expect:
            done.set()

    async def _count_audio(track) -> None:
        stream = rtc.AudioStream(track)
        async for ev in stream:
            f = ev.frame
            got["audio_frames"] += 1
            got["audio_seconds"] += f.samples_per_channel / float(f.sample_rate)

    @room.on("track_subscribed")
    def _on_track(track, publication, participant) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO and track.name == TRACK:
            got["voice_track"] = True
            tasks.append(asyncio.ensure_future(_count_audio(track)))

    t0 = time.monotonic()
    # Subscribe-only peers joined a local dev server only in single peer connection mode (tested 2026-11-08).
    opts = rtc.RoomOptions(auto_subscribe=True, single_peer_connection=True) if single_pc else rtc.RoomOptions()
    try:
        await room.connect(cfg["url"], token, opts)
    except Exception as exc:
        print("listen: could not join %s room %r: %s" % (cfg["url"], cfg["room"], exc), file=sys.stderr)
        got["error"] = str(exc)
        got["publishers"] = []
        return got
    print("listen: joined room %r as %s in %.1f s" % (cfg["room"], identity, time.monotonic() - t0),
          file=sys.stderr, flush=True)
    try:
        await asyncio.wait_for(done.wait(), timeout=max_seconds if max_seconds > 0 else None)
    except asyncio.TimeoutError:
        pass
    await asyncio.sleep(0.5)
    for t in tasks:
        t.cancel()
    await room.disconnect()
    got["publishers"] = sorted(got["publishers"])
    got["audio_seconds"] = round(got["audio_seconds"], 2)
    return got


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--identity", default="hold-my-beer-listener-%d" % int(time.time()))
    p.add_argument("--room", default=None)
    p.add_argument("--expect", type=int, default=0, help="exit after this many messages")
    p.add_argument("--max-seconds", type=float, default=0, help="exit after this long (0 = never)")
    p.add_argument("--quiet", action="store_true")
    p.add_argument("--dual-pc", action="store_true", help="classic two peer connections instead of one")
    p.add_argument("--print-token", action="store_true",
                   help="print a 12 h subscribe-only token (for a browser viewer) and exit")
    a = p.parse_args(argv)
    cfg = livekit_env()
    if cfg is None:
        print("listen: set LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET", file=sys.stderr)
        return 2
    if a.room:
        cfg["room"] = a.room
    if a.print_token:
        from livekit import api
        print(viewer_token(api, cfg, a.identity))
        return 0
    try:
        got = asyncio.run(listen(cfg, a.identity, a.expect, a.max_seconds, a.quiet, not a.dual_pc))
    except KeyboardInterrupt:
        return 0
    print(json.dumps({"summary": got}))
    return 0 if got["messages"] else 1


if __name__ == "__main__":
    sys.exit(main())
