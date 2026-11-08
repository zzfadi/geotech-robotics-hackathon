"""Offline tests for the Hold My Beer voice: no audio device, no network, no LiveKit.

  python3 -m unittest discover -s voice_agent -t . -p 'test_*.py' -v
"""
from __future__ import annotations

import asyncio
import io
import json
import re
import struct
import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

from voice_agent import bridge, play, render
from voice_agent.cues import Cuer, Event, Line, Lines, normalize, parse_json_line

ROOT = Path(__file__).resolve().parents[1]


def lines() -> Lines:
    return Lines.load()


def write_wav(path: Path, seconds: float = 0.05, rate: int = 24000) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<h", 0) * int(rate * seconds))


class Clock:
    def __init__(self, t: float = 100.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


class LinesFile(unittest.TestCase):
    def test_loads_and_every_line_is_short(self):
        L = lines()
        self.assertGreaterEqual(len(L.by_id), 20)
        for line in L.by_id.values():
            self.assertLessEqual(len(line.text.split()), 7, line.id)
            self.assertIn(line.voice, ("righty", "lefty"))

    def test_no_line_sounds_like_a_safety_instruction(self):
        banned = re.compile(r"\b(safe|safely|safety|clear|careful|caution|go ahead|your turn|reach|"
                            r"let go|hand it|don't worry|it's okay|ok to|step back|stand back|"
                            r"protect|guarantee|hurt|harm)\b", re.I)
        for line in lines().by_id.values():
            self.assertIsNone(banned.search(line.text), "%s: %r" % (line.id, line.text))

    def test_every_routine_step_and_stop_reason_is_decided(self):
        src = (ROOT / "handoff" / "routine.py").read_text()
        steps = set(re.findall(r'_step\("([a-z_]+)"', src)) | {"paused", "reset", "halted"}
        reasons = set(re.findall(r'Abort\("([a-z_]+)"', src)) - {"reset"}
        L = lines()
        self.assertIn("offer", steps)
        self.assertIn("person_kept_holding", reasons)
        for s in steps:
            self.assertNotEqual(L.resolve("step:" + s)[0], "unknown", s)
        for r in reasons:
            for outcome in ("failed", "stopped"):
                self.assertNotEqual(L.resolve("end:%s:%s" % (outcome, r))[0], "unknown", r)
        for what in ("grasp", "release", "drop"):
            self.assertEqual(L.resolve("contact:" + what)[0], "line", what)

    def test_turn_gate_and_vision_names_are_decided(self):
        gate_src = (ROOT / "turn_gate" / "gate.py").read_text()
        reasons = set(re.findall(r'_hold\("([a-z_]+)"', gate_src)) | {"clear_debounce", "awaiting_turn_request"}
        L = lines()
        for kind in ("holding", "turn_requested", "robot_turn_granted", "robot_turn_revoked",
                     "robot_turn_completed"):
            for r in reasons:
                self.assertNotEqual(L.resolve("gate:%s:%s" % (kind, r))[0], "unknown", (kind, r))
        moments_src = (ROOT / "vision" / "moments.py").read_text()
        moments = set(re.findall(r'"([a-z_]+)",?\n', moments_src.split("_MOMENTS = {")[1].split("}")[0]))
        # Transition kinds assigned in the tracker (hand_withdrawn, offer_withdrawn, hand_returned, ...).
        for row in re.findall(r"\bkind = (.+)", moments_src):
            moments |= set(re.findall(r'"([a-z_]+)"', row))
        self.assertIn("can_released", moments)
        self.assertIn("offer_withdrawn", moments)
        for m in moments:
            self.assertNotEqual(L.resolve("moment:" + m)[0], "unknown", m)

    def test_every_replay_scenario_resolves_every_event(self):
        from handoff.replay import SCENARIOS, run_scenario
        L = lines()
        said = set()
        for path in sorted(SCENARIOS.glob("*.json")):
            sc = json.loads(path.read_text())
            result = run_scenario(sc)
            for ev in result.events:
                for e in normalize(ev):
                    status, line = L.resolve(e.key)
                    self.assertNotEqual(status, "unknown", "%s: %s" % (path.stem, e.key))
                    if line:
                        said.add(line.id)
        for must in ("watch_this", "beer_me", "say_less", "thank_you", "held", "show_off", "little_help",
                     "keep_it", "that_was_mine", "scene", "overheating"):
            self.assertIn(must, said)

    def test_lookup_order(self):
        L = lines()
        self.assertEqual(L.resolve("step:offer:1")[1].id, "beer_me")
        self.assertEqual(L.resolve("step:offer:2"), ("silent", None))
        self.assertEqual(L.resolve("step:retreat:resumed")[1].id, "were_back")
        self.assertEqual(L.resolve("step:paused:approach")[1].id, "hi_there")
        self.assertEqual(L.resolve("end:failed:something_new")[1].id, "that_happened")
        self.assertEqual(L.resolve("end:success:ready"), ("silent", None))
        self.assertEqual(L.resolve("nonsense:thing"), ("unknown", None))

    def test_bad_files_are_rejected(self):
        base = {"voices": {"a": {}}, "lines": [{"id": "x", "text": "Hi.", "voice": "a", "on": ["k"]}]}
        with self.assertRaises(ValueError):
            Lines(dict(base, lines=base["lines"] * 2))
        with self.assertRaises(ValueError):
            Lines(dict(base, lines=[{"id": "x", "text": "Hi.", "voice": "zz"}]))


class Normalize(unittest.TestCase):
    def test_handoff_run_stderr_line(self):
        rec = parse_json_line('  {"kind": "step", "step": "offer", "detail": "attempt 2"}\n')
        self.assertEqual([e.key for e in normalize(rec)], ["step:offer:2"])

    def test_task_end_and_reset(self):
        end = {"t": 9.0, "kind": "task", "phase": "end", "name": "Take the can from a person's hand",
               "outcome": "failed", "detail": "no_grasp"}
        reset = dict(end, name="Reset to the ready pose", outcome="success", detail="ready")
        self.assertEqual(normalize(end)[0].key, "end:failed:no_grasp")
        self.assertEqual(normalize(end)[0].t, 9.0)
        self.assertEqual(normalize(reset)[0].key, "task:reset_end:success")

    def test_vision_frame_moments_only(self):
        frame = {"type": "frame", "moment": {"captured_at": 2.5, "events": [
            {"at": 2.5, "kind": "can_released", "reason": "hand_let_go", "state": "RELEASED"}]},
            "actions": [{"action": "retreat_with_can", "grant_id": 1}], "gate_events": []}
        self.assertEqual([e.key for e in normalize(frame)], ["moment:can_released", "vision_action:retreat_with_can"])
        self.assertEqual(normalize({"type": "frame", "moment": {"events": []}, "actions": []}), [])
        self.assertEqual(normalize({"type": "session_summary", "frames": 10}), [])

    def test_turn_gate_replay_record_and_bare_event(self):
        rec = {"action": "observe", "events": [{"at": 0.55, "grant_id": 1, "kind": "robot_turn_revoked",
                                                "reason": "hand_present", "state": "HOLD"}]}
        self.assertEqual(normalize(rec)[0].key, "gate:robot_turn_revoked:hand_present")
        self.assertEqual(normalize(rec["events"][0])[0].key, "gate:robot_turn_revoked:hand_present")

    def test_trial_row_and_garbage(self):
        self.assertEqual(normalize({"mode": "hand", "trial": 1, "outcome": "success", "reason": "held"})[0].key,
                         "trial:success:held")
        self.assertIsNone(parse_json_line("Trial 1 of 10 (hand). Type g + Enter"))
        self.assertIsNone(parse_json_line("{not json"))
        self.assertEqual(normalize({"hello": 1}), [])


class Cueing(unittest.TestCase):
    def test_same_line_once_within_cooldown(self):
        c = Cuer(lines())
        ev1 = Event("handoff", "contact:release")
        ev2 = Event("vision", "moment:can_released")
        self.assertEqual(c.cue(ev1, 10.0).id, "thank_you")
        self.assertIsNone(c.cue(ev2, 10.5))
        self.assertEqual(c.why, "repeat")
        self.assertEqual(c.cue(ev2, 30.0).id, "thank_you")

    def test_busy_drops_ordinary_lines_not_urgent_ones(self):
        c = Cuer(lines())
        self.assertIsNone(c.cue(Event("handoff", "step:offer:1"), 1.0, pending=2))
        self.assertEqual(c.why, "busy")
        self.assertEqual(c.cue(Event("handoff", "end:stopped:operator_stop"), 1.0, pending=5).id, "scene")

    def test_silent_and_unknown(self):
        c = Cuer(lines())
        self.assertIsNone(c.cue(Event("handoff", "state"), 1.0))
        self.assertEqual(c.why, "silent")
        self.assertIsNone(c.cue(Event("x", "brand_new"), 1.0))
        self.assertEqual(c.why, "unknown")


class LocalPlayback(unittest.TestCase):
    def test_plays_wav_with_afplay_and_falls_back_to_say(self):
        with tempfile.TemporaryDirectory() as d:
            L = lines()
            write_wav(Path(d) / "say_less.wav")
            ran = []
            p = play.LocalPlayer(L, Path(d), run=ran.append, sleep=lambda s: None,
                                 which=lambda t: t == "afplay")
            p.play_one(L.by_id["say_less"], p.clock())
            p.play_one(L.by_id["beer_me"], p.clock())
            self.assertEqual(ran[0], ["afplay", str(Path(d) / "say_less.wav")])
            self.assertEqual(ran[1][0], "say")
            self.assertEqual(ran[1][-1], "Beer me.")
            linux = play.LocalPlayer(L, Path(d), which=lambda t: t == "aplay")
            self.assertEqual(linux.command(L.by_id["say_less"])[0], "aplay")

    def test_stale_lines_drop_and_urgent_clears_the_queue(self):
        L = lines()
        clock = Clock()
        ran = []
        p = play.LocalPlayer(L, Path("/nonexistent"), run=ran.append, clock=clock, sleep=lambda s: None)
        p.submit(L.by_id["beer_me"])
        p.submit(L.by_id["say_less"])
        p.submit(L.by_id["scene"])
        self.assertEqual(p.dropped, ["beer_me", "say_less"])
        line, queued = p.q.get_nowait()
        clock.t += 60
        self.assertTrue(p.play_one(line, queued))      # urgent: never stale
        self.assertFalse(p.play_one(L.by_id["held"], clock.t - 10))
        self.assertEqual(p.played, ["scene"])

    def test_dry_run_and_pace(self):
        L = lines()
        out = io.StringIO()
        p = play.LocalPlayer(L, Path("/nonexistent"), dry_run=True, run=lambda c: self.fail("played"),
                             sleep=lambda s: None)
        slept = []
        stream = ['{"t": 1.0, "kind": "step", "step": "waiting_for_go", "detail": ""}',
                  '{"t": 3.5, "kind": "step", "step": "offer", "detail": "attempt 1"}',
                  'noise']
        n = play.run_local(play.records(stream, pace=True, sleep=slept.append), L, p, out=out)
        self.assertEqual(n, 2)
        self.assertEqual(slept, [2.5])
        self.assertEqual([json.loads(x)["cue"] for x in out.getvalue().splitlines()], ["that_look", "beer_me"])

    def test_follow_file_reads_appended_lines(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "events.log"
            path.write_text('{"old": true}\n')
            stop = threading.Event()
            got = []

            def reader():
                for line in play.follow_file(path, poll_s=0.01, stop=stop.is_set):
                    got.append(line)
                    if len(got) == 2:
                        stop.set()

            t = threading.Thread(target=reader, daemon=True)
            t.start()
            time.sleep(0.05)
            with path.open("a") as fh:
                fh.write('{"kind": "contact", ')
                fh.flush()
                time.sleep(0.05)
                fh.write('"what": "grasp"}\n{"kind": "step", "step": "done"}\n')
            t.join(2)
            self.assertEqual([json.loads(x)["kind"] for x in got], ["contact", "step"])


class Rendering(unittest.TestCase):
    def test_render_commands_manifest_and_skip_unchanged(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d)
            cmds = []

            def runner(cmd):
                cmds.append(list(cmd))
                if cmd[0] == "afconvert":
                    write_wav(Path(cmd[-1]))
                if cmd[0] == "say":
                    Path(cmd[2]).write_bytes(b"aiff")

            L = lines()
            m = render.render(L, out, runner=runner, installed=["Rocko (English (US))", "Samantha (English (US))"])
            says = [c for c in cmds if c[0] == "say"]
            self.assertEqual(len(says), len(L.by_id))
            first = says[0]
            self.assertIn("-v", first)
            self.assertIn("--", first)
            self.assertEqual(len(m["items"]), len(L.by_id))
            self.assertTrue(all(i["seconds"] for i in m["items"]))
            self.assertFalse(list(out.glob("*.aiff")))
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(manifest["sample_rate"], 24000)
            cmds.clear()
            render.render(L, out, runner=runner, installed=["Rocko (English (US))", "Samantha (English (US))"])
            self.assertEqual(cmds, [])

    def test_pick_voice(self):
        self.assertEqual(render.pick_voice(["Nope", "Fred"], ["Fred", "Kathy"]), "Fred")
        self.assertIsNone(render.pick_voice(["Nope"], ["Fred"]))
        self.assertEqual(render.pick_voice("Fred", []), "Fred")


# ------------------------------------------------------------- LiveKit fakes
class FakeGrants:
    def __init__(self, **kw):
        self.kw = kw


class FakeToken:
    made = []

    def __init__(self, key, secret):
        self.key, self.secret, self.calls = key, secret, {}
        FakeToken.made.append(self)

    def __getattr__(self, name):
        if name.startswith("with_"):
            def setter(value):
                self.calls[name] = value
                return self
            return setter
        raise AttributeError(name)

    def to_jwt(self):
        return "jwt-for-%s" % self.calls.get("with_identity")


class FakeApi:
    VideoGrants = FakeGrants
    AccessToken = FakeToken


class FakeParticipant:
    def __init__(self, fail_data=False):
        self.data, self.tracks, self.fail_data = [], [], fail_data

    async def publish_data(self, payload, *, reliable=True, destination_identities=(), topic=""):
        if self.fail_data:
            raise RuntimeError("room gone")
        self.data.append((json.loads(payload), reliable, topic))

    async def publish_track(self, track, opts):
        self.tracks.append((track, opts))


class FakeRoom:
    def __init__(self):
        self.local_participant = FakeParticipant()
        self.connected = None
        self.disconnected = False

    async def connect(self, url, token):
        self.connected = (url, token)

    async def disconnect(self):
        self.disconnected = True


class FakeSource:
    def __init__(self, rate, channels):
        self.rate, self.channels, self.frames = rate, channels, []

    async def capture_frame(self, frame):
        self.frames.append(frame)

    async def wait_for_playout(self):
        return None


class FakeFrame:
    def __init__(self, data, rate, channels, n):
        assert len(data) == n * channels * 2
        self.n = n


class FakeRtc:
    Room = FakeRoom
    AudioSource = FakeSource
    AudioFrame = FakeFrame

    class LocalAudioTrack:
        @staticmethod
        def create_audio_track(name, source):
            return ("track", name)

    class TrackSource:
        SOURCE_MICROPHONE = 2

    @staticmethod
    def TrackPublishOptions(source):
        return {"source": source}


class FakeLink:
    def __init__(self, fail=False):
        self.sent, self.spoken_files, self.fail = [], [], fail
        self.spoken = 0

    async def send(self, msg, reliable=True):
        if self.fail:
            raise RuntimeError("down")
        self.sent.append((msg, reliable))

    async def speak(self, wav):
        self.spoken_files.append(wav.name)
        self.spoken += 1
        return True

    async def close(self):
        pass


def run_async(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class LiveKitBridge(unittest.TestCase):
    def test_env_needs_all_three(self):
        self.assertIsNone(bridge.livekit_env({"LIVEKIT_URL": "ws://x", "LIVEKIT_API_KEY": "k"}))
        cfg = bridge.livekit_env({"LIVEKIT_URL": "ws://x", "LIVEKIT_API_KEY": "k", "LIVEKIT_API_SECRET": "s"})
        self.assertEqual(cfg["room"], "hold-my-beer")
        cfg = bridge.livekit_env({"LIVEKIT_URL": "ws://x", "LIVEKIT_API_KEY": "k", "LIVEKIT_API_SECRET": "s",
                                  "LIVEKIT_ROOM": "stage"})
        self.assertEqual(cfg["room"], "stage")

    def test_token_is_publish_only(self):
        jwt = bridge.mint_token(FakeApi, "key", "secret", "hold-my-beer")
        tok = FakeToken.made[-1]
        self.assertEqual(jwt, "jwt-for-hold-my-beer-voice")
        g = tok.calls["with_grants"].kw
        self.assertEqual(g["room"], "hold-my-beer")
        self.assertTrue(g["room_join"] and g["can_publish"] and g["can_publish_data"])
        self.assertFalse(g["can_subscribe"])

    def test_link_publishes_track_data_and_audio_frames(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "x.wav"
            write_wav(wav, seconds=0.105)   # 2520 samples -> 11 frames of 240, last one padded
            link = bridge.LiveKitLink(FakeRtc, "ws://localhost:7880", "jwt")

            async def go():
                await link.connect()
                await link.send({"key": "step:offer:1"})
                ok = await link.speak(wav)
                await link.close()
                return ok

            self.assertTrue(run_async(go()))
            lp = link.room.local_participant
            self.assertEqual(link.room.connected, ("ws://localhost:7880", "jwt"))
            self.assertEqual(lp.tracks[0][0], ("track", "hold-my-beer-voice"))
            self.assertEqual(lp.data, [({"key": "step:offer:1"}, True, "handoff")])
            self.assertEqual(len(link.source.frames), 11)
            self.assertTrue(link.room.disconnected)

    def test_link_refuses_wrong_sample_rate(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "x.wav"
            write_wav(wav, rate=22050)
            link = bridge.LiveKitLink(FakeRtc, "ws://x", "jwt")

            async def go():
                await link.connect()
                return await link.speak(wav)
            self.assertFalse(run_async(go()))

    def _bridge_run(self, link, stream, audio_dir):
        L = lines()
        ran = []
        player = play.LocalPlayer(L, audio_dir, run=ran.append, sleep=lambda s: None)
        out = io.StringIO()

        async def go():
            b = bridge.Bridge(L, link, player, audio_dir, out=out)
            q = asyncio.Queue()
            for text in stream:
                for ev in play.records([text]):
                    q.put_nowait(ev)
            q.put_nowait(None)
            await b.run(q)
            return b
        b = run_async(go())
        return b, player, out

    def test_bridge_publishes_every_event_and_speaks_lines(self):
        with tempfile.TemporaryDirectory() as d:
            write_wav(Path(d) / "beer_me.wav")
            link = FakeLink()
            stream = ['{"t": 5.3, "kind": "step", "step": "offer", "detail": "attempt 1"}',
                      '{"t": 5.4, "kind": "state", "q": [0, 0, 0, 0, 0, 0], "grip": -90}',
                      '{"t": 5.5, "kind": "state", "q": [0, 0, 0, 0, 0, 1], "grip": -90}',
                      '{"t": 6.0, "kind": "gate", "event": "robot_turn_revoked", "reason": "hand_present", "grant": 1}']
            b, player, out = self._bridge_run(link, stream, Path(d))
            keys = [m["key"] for m, _ in link.sent]
            self.assertEqual(keys, ["step:offer:1", "state", "handoff_gate:robot_turn_revoked:hand_present"])
            first, reliable = link.sent[0]
            self.assertTrue(reliable)
            self.assertEqual(first["schema"], "hold-my-beer/event/1")
            self.assertEqual(first["line"], {"id": "beer_me", "text": "Beer me.", "voice": "righty"})
            self.assertEqual(first["event"]["step"], "offer")
            self.assertEqual(first["seq"], 1)
            self.assertFalse(link.sent[1][1])          # state goes unreliable, throttled
            self.assertIsNone(link.sent[2][0]["line"])
            self.assertEqual(link.spoken_files, ["beer_me.wav"])
            self.assertEqual(json.loads(out.getvalue().splitlines()[0])["cue"], "beer_me")
            self.assertEqual(player.pending(), 1)       # queued for the local speaker too

    def test_vision_executor_actions_never_reach_the_room(self):
        with tempfile.TemporaryDirectory() as d:
            link = FakeLink()
            frame = {"type": "frame", "moment": {"captured_at": 2.5, "events": [
                {"at": 2.5, "kind": "offer_steady", "reason": "x", "state": "ENGAGED"}]},
                "actions": [{"action": "start_handoff", "grant_id": 1, "executor": "dream"},
                            {"action": "abort", "grant_id": 1, "reason": "hand_present"}],
                "gate_events": []}
            self._bridge_run(link, [json.dumps(frame)], Path(d))
            keys = [m["key"] for m, _ in link.sent]
            self.assertEqual(keys, ["moment:offer_steady"])
            self.assertNotIn("start_handoff", json.dumps([m for m, _ in link.sent]))

    def test_room_failure_falls_back_to_local(self):
        with tempfile.TemporaryDirectory() as d:
            link = FakeLink(fail=True)
            stream = ['{"kind": "contact", "what": "grasp", "object": "can"}',
                      '{"kind": "step", "step": "done"}']
            with mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                b, player, out = self._bridge_run(link, stream, Path(d))
            self.assertIsNone(b.link)
            self.assertIn("local playback only", err.getvalue())
            self.assertEqual([json.loads(x)["cue"] for x in out.getvalue().splitlines()], ["say_less", "held"])
            self.assertEqual(player.pending(), 2)

    def test_main_without_env_is_local_only(self):
        with tempfile.TemporaryDirectory() as d:
            trace = Path(d) / "t.jsonl"
            trace.write_text('{"kind": "contact", "what": "grasp", "object": "can"}\n')
            env = {k: v for k, v in __import__("os").environ.items() if not k.startswith("LIVEKIT_")}
            with mock.patch.dict("os.environ", env, clear=True), \
                    mock.patch("sys.stdout", new_callable=io.StringIO) as out, \
                    mock.patch("sys.stderr", new_callable=io.StringIO) as err:
                rc = bridge.main([str(trace), "--dry-run", "--audio", d])
            self.assertEqual(rc, 0)
            self.assertIn("local playback only", err.getvalue())
            self.assertEqual(json.loads(out.getvalue().splitlines()[0])["cue"], "say_less")


class OneWay(unittest.TestCase):
    def test_nothing_here_can_reach_the_arm(self):
        """The voice only announces: no arm client, no RPC handlers, no inbound room data."""
        forbidden = ("arm_link", "SocketArm", "pickup_client", "pickup_server", "8765", "perform_rpc",
                     "register_rpc_method", "complete_turn", "request_turn")
        for path in Path(__file__).parent.glob("*.py"):
            if path.name.startswith("test_"):
                continue
            src = path.read_text()
            for word in forbidden:
                self.assertNotIn(word, src, "%s mentions %s" % (path.name, word))
        self.assertNotIn("data_received", (Path(__file__).parent / "bridge.py").read_text())


if __name__ == "__main__":
    unittest.main()
