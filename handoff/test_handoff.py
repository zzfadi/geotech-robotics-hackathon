"""python3 -m unittest discover -s handoff -t . -p 'test_*.py' -v"""
import json
import socket
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from handoff import replay, trials
from handoff.arm_link import ArmError, FakeArm, FakeClock, SocketArm
from handoff.config import load
from handoff.detections import Fuser, Zones, facts_from_boxes, flags_from_vision
from handoff.routine import Routine, ScriptedOperator

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = sorted((ROOT / "handoff" / "scenarios").glob("*.json"))


def scenario(name):
    return json.loads((ROOT / "handoff" / "scenarios" / (name + ".json")).read_text())


class Scenarios(unittest.TestCase):
    def test_every_scenario_meets_its_expectation(self):
        self.assertGreaterEqual(len(SCENARIOS), 15)
        for path in SCENARIOS:
            with self.subTest(path.stem):
                sc = json.loads(path.read_text())
                result = replay.run_scenario(sc)
                self.assertEqual(replay.check(sc, result), [])

    def _moves(self, sc):
        """Run a scenario and return the times the fake arm started each move."""
        cfg = load()
        clock = FakeClock()
        segments = replay._segments(sc["timeline"])
        fuser = Fuser(stale_after_s=cfg.timing.stale_after_s)
        feed = replay.ReplayFeed(segments, fuser, clock, 15)
        arm = FakeArm(clock, cfg.poses["ready"].q,
                      can_between_jaws=lambda t: bool((replay._at(segments, t) or {}).get("can_in_jaws")))
        r = Routine(cfg, arm, fuser, ScriptedOperator(clock, sc["operator"]), clock, before_read=feed.pump)
        result = r.run()
        starts = [c["t"] for c in arm.calls if c["cmd"] == "move"]
        return result, starts

    def test_no_move_starts_while_a_hand_is_in_the_path(self):
        result, starts = self._moves(scenario("hand_path_blocked_resume"))
        self.assertEqual(result.outcome, "success")
        # Hand in the path from 3.0 to 4.5 s, resume said at 6.0 s. A move that
        # started just before 3.0 may finish (one segment); none may start inside.
        self.assertFalse([t for t in starts if 3.05 <= t < 6.0], starts)

    def test_no_move_starts_while_the_feed_is_stale_or_invalid(self):
        result, starts = self._moves(scenario("hand_detector_dropout"))
        self.assertFalse([t for t in starts if 3.3 <= t < 6.0], starts)

    def test_parked_arm_never_moves_while_waiting_for_the_can(self):
        sc = scenario("hand_ok")
        result, starts = self._moves(sc)
        offer_done = next(e["t"] for e in result.events if e.get("step") == "offer")
        grasp = next(e["t"] for e in result.events if e.get("kind") == "contact" and e["what"] == "release")
        self.assertFalse([t for t in starts if offer_done <= t < grasp])

    def test_operator_stop_sends_nothing_after_the_stop(self):
        cfg = load()
        clock = FakeClock()
        sc = scenario("hand_operator_stop")
        segments = replay._segments(sc["timeline"])
        fuser = Fuser()
        feed = replay.ReplayFeed(segments, fuser, clock, 15)
        arm = FakeArm(clock, cfg.poses["ready"].q)
        Routine(cfg, arm, fuser, ScriptedOperator(clock, sc["operator"]), clock, before_read=feed.pump).run()
        self.assertFalse([c for c in arm.calls if c["t"] > 3.6 and c["cmd"] != "status"])

    def test_replay_writes_valid_control_center_sessions(self):
        sys.path.insert(0, str(ROOT))
        from control_center.telemetry import read, validate
        with tempfile.TemporaryDirectory() as d:
            code = replay.main([str(ROOT / "handoff/scenarios/hand_ok.json"), "--telemetry", d])
            self.assertEqual(code, 0)
            (session,) = Path(d).iterdir()
            events, _ = read(str(session / "events.jsonl"))
            self.assertTrue(events)
            self.assertEqual([p for e in events for p in validate(e)], [])
            self.assertEqual(events[-2]["outcome"], "success")


class Detections(unittest.TestCase):
    zones = Zones(path=(0.0, 0.0, 0.5, 1.0), jaws=(0.3, 0.3, 0.7, 0.8))

    def test_can_centred_and_close_with_a_hand_on_it(self):
        dets = [{"label": "can", "box": [0.35, 0.3, 0.65, 0.8], "conf": 0.9},
                {"label": "hand", "box": [0.55, 0.4, 0.9, 0.7], "conf": 0.8}]
        f = facts_from_boxes(dets, self.zones, path=False, jaws=True)
        self.assertTrue(f["can_in_jaws"])
        self.assertTrue(f["hand_on_can"])

    def test_far_away_can_is_not_in_the_jaws(self):
        f = facts_from_boxes([{"label": "can", "box": [0.48, 0.5, 0.52, 0.6], "conf": 0.9}],
                             self.zones, path=False, jaws=True)
        self.assertFalse(f["can_in_jaws"])

    def test_low_confidence_and_bad_boxes_are_ignored(self):
        dets = [{"label": "hand", "box": [0.1, 0.1, 0.2, 0.2], "conf": 0.1},
                {"label": "hand", "box": [0.2, 0.1, 0.1, 0.2], "conf": 0.9}]
        self.assertFalse(facts_from_boxes(dets, self.zones, path=True, jaws=False)["hand_in_path"])

    def test_fuser_scene_is_invalid_when_a_role_is_stale(self):
        f = Fuser(Zones(path_camera="head", jaw_camera="wrist_r"), stale_after_s=0.25)
        f.push({"captured_at": 1.0, "camera": "head", "detections": []})
        f.push({"captured_at": 1.2, "camera": "wrist_r", "detections": []})
        self.assertTrue(f.scene(1.2).valid)
        self.assertEqual(f.scene(1.3).reason, "stale")
        self.assertEqual(f.scene(1.3).captured_at, 1.0)

    def test_repeated_frame_never_refreshes(self):
        f = Fuser()
        f.push({"captured_at": 1.0, "valid": True, "hand_in_path": False})
        f.push({"captured_at": 1.0, "valid": True, "hand_in_path": True})
        self.assertEqual(len(f.history("path", None)), 1)
        self.assertFalse(f.history("path", None)[0].hand_in_path)

    def test_failed_inference_is_invalid_not_clear(self):
        f = Fuser()
        f.push({"captured_at": 2.0, "camera": "head", "valid": False, "detections": []})
        self.assertFalse(f.history("path", None)[0].valid)

    def test_vision_records_map_onto_handoff_facts(self):
        out = subprocess.run([sys.executable, "-m", "vision.run", "--replay",
                              "vision/examples/handoff.jsonl", "--target", "real", "--verbose"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
        seen = []
        for line in out.splitlines():
            rec = json.loads(line)
            flags = flags_from_vision(rec) if rec.get("type") == "frame" else None
            if flags and flags["valid"]:
                key = (flags["can_in_jaws"], flags["hand_on_can"])
                if not seen or seen[-1] != key:
                    seen.append(key)
        # empty -> hand holding a can, not yet steady -> steady offer -> let go
        self.assertEqual(seen, [(False, False), (False, True), (True, True), (True, False)])

    def test_invalid_vision_moment_is_invalid(self):
        self.assertEqual(flags_from_vision({"moment": {"captured_at": 1.0, "valid": False,
                                                       "state": "INVALID", "motion_ok": None}}),
                         {"captured_at": 1.0, "valid": False})


class OcclusionFailSafe(unittest.TestCase):
    """A hand that vanishes from view while on the can is not a release."""

    def _run(self, sc):
        cfg = load()
        clock = FakeClock(limit=300)
        segments = replay._segments(sc["timeline"])
        fuser = Fuser(stale_after_s=cfg.timing.stale_after_s)
        feed = replay.ReplayFeed(segments, fuser, clock, 15)
        arm = FakeArm(clock, cfg.poses["ready"].q,
                      can_between_jaws=lambda t: bool((replay._at(segments, t) or {}).get("can_in_jaws")))
        result = Routine(cfg, arm, fuser, ScriptedOperator(clock, sc["operator"]), clock,
                         before_read=feed.pump).run()
        return cfg, result, arm.calls

    def test_hidden_hand_never_carries_the_can_away(self):
        for name in ("hand_occluded_while_holding", "hand_lens_covered_while_holding",
                     "hand_seen_off_then_lens_covered"):
            with self.subTest(name):
                cfg, result, calls = self._run(scenario(name))
                self.assertEqual((result.outcome, result.reason), ("failed", "release_not_seen"))
                steps = [e["step"] for e in result.events if e["kind"] == "step"]
                self.assertNotIn("retreat", steps)
                self.assertNotIn("confirm", steps)
                # After the grasp, nothing moves until the jaws are open again.
                grips = [i for i, c in enumerate(calls) if c["cmd"] == "grip"]
                close = next(i for i in grips if calls[i]["deg"] == cfg.gripper.close_deg)
                reopen = next(i for i in grips if i > close and calls[i]["deg"] == cfg.gripper.open_deg)
                self.assertFalse([c for c in calls[close:reopen] if c["cmd"] == "move"])
                # And it waited the full release timeout before giving the can back.
                self.assertGreaterEqual(calls[reopen]["t"] - calls[close]["t"],
                                        cfg.gripper.close_wait_s + cfg.timing.release_timeout_s - 0.1)

    def test_hidden_then_seen_letting_go_completes(self):
        sc = {"operator": [[1.0, "go"]], "timeline": [
            {"from": 0, "to": 8},
            {"from": 8, "to": 14, "can_in_jaws": True, "hand_on_can": True},
            {"from": 14, "to": 17, "can_in_jaws": True, "hand_on_can": False, "hand_visible": False},
            {"from": 17, "to": 60, "can_in_jaws": True, "hand_on_can": False, "hand_visible": True}]}
        _, result, _ = self._run(sc)
        self.assertEqual((result.outcome, result.reason), ("success", "held"))
        release = next(e["t"] for e in result.events if e["kind"] == "contact" and e["what"] == "release")
        self.assertGreaterEqual(release, 17.0)

    def test_letting_go_during_the_close_counts(self):
        # Seen off the can during the 3 s close wait, then out of view: released.
        sc = scenario("hand_let_go_during_close")
        cfg, result, calls = self._run(sc)
        self.assertEqual((result.outcome, result.reason), ("success", "held"))
        close = next(c["t"] for c in calls if c["cmd"] == "grip" and c["deg"] == cfg.gripper.close_deg)
        self.assertLess(close, 9.5)   # the hand let go after the close started

    def test_a_stale_feed_drops_a_seen_release(self):
        # Seen off for 0.2 s, then frames stop (feed restart), then the hand is
        # out of view: the glimpse no longer counts, so no carry.
        for name, timeline in (
            ("after the close", [
                {"from": 0, "to": 8},
                {"from": 8, "to": 14, "can_in_jaws": True, "hand_on_can": True},
                {"from": 14, "to": 14.2, "can_in_jaws": True, "hand_on_can": False, "hand_visible": True},
                {"from": 14.2, "to": 14.7, "missing": True},
                {"from": 14.7, "to": 80, "missing": False, "hand_visible": False}]),
            ("during the close", [
                {"from": 0, "to": 8},
                {"from": 8, "to": 9.5, "can_in_jaws": True, "hand_on_can": True},
                {"from": 9.5, "to": 9.8, "can_in_jaws": True, "hand_on_can": False, "hand_visible": True},
                {"from": 9.8, "to": 10.3, "missing": True},
                {"from": 10.3, "to": 80, "missing": False, "hand_visible": False}])):
            with self.subTest(name):
                _, result, _ = self._run({"operator": [[1.0, "go"]], "timeline": timeline})
                self.assertEqual((result.outcome, result.reason), ("failed", "release_not_seen"))
                steps = [e["step"] for e in result.events if e["kind"] == "step"]
                self.assertNotIn("retreat", steps)

    def test_release_predicate(self):
        from handoff.detections import Scene
        released, watch = Routine._release_evidence()
        on = Scene(1.0, True, False, True, True)
        gone = Scene(1.1, True, False, True, False, hand_visible=False)
        seen_off = Scene(1.2, True, False, True, False, hand_visible=True)
        unknown = Scene(1.3, True, False, True, False)
        self.assertFalse(released(on))
        self.assertFalse(released(gone))
        self.assertTrue(watch["unseen"])
        self.assertFalse(released(unknown))
        self.assertTrue(released(seen_off))
        self.assertTrue(released(gone))          # seen letting go, then left the view
        self.assertFalse(released(on))           # back on the can: start over
        self.assertFalse(released(gone))
        self.assertFalse(released(Scene(1.4, False)))
        covered = Scene(1.5, True, False, False, False, hand_visible=False)
        self.assertTrue(released(seen_off))
        self.assertFalse(released(covered))      # no can in view (covered lens): drop the release
        self.assertEqual(watch, {"seen_off": False, "unseen": True})
        self.assertFalse(released(gone))         # it must be seen again
        self.assertFalse(released(Scene(1.6, True, False, None, False, hand_visible=True)))

    def test_boxes_and_vision_report_hand_visible(self):
        zones = Zones(jaws=(0.3, 0.3, 0.7, 0.8))
        can = {"label": "can", "box": [0.35, 0.3, 0.65, 0.8], "conf": 0.9}
        far_hand = {"label": "hand", "box": [0.0, 0.0, 0.1, 0.1], "conf": 0.9}
        self.assertFalse(facts_from_boxes([can], zones, path=False, jaws=True)["hand_visible"])
        f = facts_from_boxes([can, far_hand], zones, path=False, jaws=True)
        self.assertEqual((f["hand_visible"], f["hand_on_can"]), (True, False))
        self.assertFalse(facts_from_boxes([], zones, path=False, jaws=True)["hand_visible"])

        def vision(state, reason):
            return flags_from_vision({"moment": {"captured_at": 1.0, "valid": True, "state": state,
                                                 "reason": reason, "motion_ok": True}})
        self.assertEqual(vision("RELEASED", "can_without_hand")["hand_visible"], False)
        self.assertEqual(vision("RELEASED", "hand_let_go")["hand_visible"], True)
        self.assertEqual(vision("EMPTY", "no_hand_in_keep_out")["hand_visible"], False)
        self.assertEqual(vision("ENGAGED", "holding_offer")["hand_visible"], True)
        self.assertNotIn("hand_visible", vision("RELEASED", None))

    def test_vision_occlusion_through_the_fuser_is_not_seen_off(self):
        f = Fuser()
        f.push({"moment": {"captured_at": 1.0, "valid": True, "state": "ENGAGED", "reason": "holding_offer",
                           "motion_ok": True}})
        f.push({"moment": {"captured_at": 1.1, "valid": True, "state": "RELEASED", "reason": "can_without_hand",
                           "motion_ok": True}})
        f.push({"moment": {"captured_at": 1.2, "valid": True, "state": "RELEASED", "reason": "hand_let_go",
                           "motion_ok": True}})
        seen = [sc.hand_seen_off_can for sc in f.history("jaws", None)]
        self.assertEqual(seen, [False, False, True])


class Server(socketserver.StreamRequestHandler):
    def handle(self):
        for line in self.rfile:
            c = json.loads(line)
            if c["cmd"] == "move" and c["q"][0] > 100:
                r = {"ok": False, "error": "ValueError: joint_1 target outside limits", "q_deg": [0] * 7}
            else:
                r = {"ok": True, "q_deg": c.get("q", [1, 2, 3, 4, 5, 6]) + [-90], "echo": c}
            self.wfile.write((json.dumps(r) + "\n").encode())


class SocketArmTests(unittest.TestCase):
    def setUp(self):
        self.server = socketserver.TCPServer(("127.0.0.1", 0), Server)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.arm = SocketArm(self.server.server_address[1])

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_speaks_the_pickup_server_protocol(self):
        r = self.arm.move([1, 2, 3, 4, 5, 6], 12)
        self.assertEqual(r["echo"], {"cmd": "move", "q": [1, 2, 3, 4, 5, 6], "speed": 12})
        self.assertEqual(self.arm.grip(-2.5, 3)["echo"], {"cmd": "grip", "deg": -2.5, "wait": 3})

    def test_refusal_raises_with_the_reply(self):
        with self.assertRaises(ArmError) as cm:
            self.arm.move([150, 0, 0, 0, 0, 0], 5)
        self.assertIn("outside limits", str(cm.exception))

    def test_only_localhost(self):
        with self.assertRaises(ValueError):
            SocketArm(8765, host="10.0.0.5")


class ConfigAndTools(unittest.TestCase):
    def test_committed_poses_are_untaught_placeholders(self):
        cfg = load()
        if any(p.taught for p in cfg.poses.values()) or cfg.gripper.contact_calibrated:
            self.skipTest("handoff/poses.json has been taught at the station; the placeholder check no longer applies")
        self.assertEqual(set(cfg.untaught("hand")), {"ready", "offer", "carry"})
        self.assertFalse(cfg.gripper.contact_calibrated)

    @staticmethod
    def _live_args(d: str, poses: Path) -> list:
        # Never the station's poses.json (taught at the station), its trial log or
        # port 8765: if a refusal ever broke, this must not reach the real arm.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return ["--operator-cutoff", "--poses", str(poses), "--port", str(port),
                "--trial-log", str(Path(d) / "trials.jsonl"), "--telemetry", str(Path(d) / "sessions")]

    def test_live_runner_refuses_untaught_poses(self):
        from handoff import run
        raw = json.loads((ROOT / "handoff/poses.json").read_text())
        for pose in raw["poses"].values():
            pose["taught"] = False
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "poses.json"
            path.write_text(json.dumps(raw))
            self.assertEqual(run.main(["--mode", "mark", "--feed", "operator"] + self._live_args(d, path)), 2)

    def test_live_runner_refuses_hand_mode_without_a_measured_band(self):
        import io
        from contextlib import redirect_stderr
        from handoff import run
        raw = json.loads((ROOT / "handoff/poses.json").read_text())
        for pose in raw["poses"].values():
            pose["taught"] = True
        raw["gripper"]["contact_calibrated"] = False
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "poses.json"
            path.write_text(json.dumps(raw))
            err = io.StringIO()
            with redirect_stderr(err):
                code = run.main(["--mode", "hand", "--feed", "-"] + self._live_args(d, path))
        self.assertEqual(code, 2)
        self.assertIn("contact band is not measured", err.getvalue())

    def test_gripper_degrees_must_be_ordered(self):
        raw = json.loads((ROOT / "handoff/poses.json").read_text())
        raw["gripper"]["contact_max_deg"] = -5.0
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(raw, fh)
        with self.assertRaises(ValueError):
            load(Path(fh.name))

    def test_morning_rule(self):
        rows = [{"mode": "hand", "outcome": "success"}] * 8 + [{"mode": "hand", "outcome": "failed",
                                                                "reason": "no_grasp"}] * 2
        self.assertIn("demo it", trials.tally(rows)["verdict"])
        rows = rows[2:] + [{"mode": "hand", "outcome": "failed", "reason": "no_grasp"}] * 2
        self.assertIn("demo the taped mark", trials.tally(rows)["verdict"])
        self.assertIn("only 3", trials.tally(rows[:3])["verdict"])
        self.assertIn("only 0", trials.tally([{"mode": "hand", "dry_run": True}])["verdict"])


if __name__ == "__main__":
    unittest.main()
