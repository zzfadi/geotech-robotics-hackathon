"""python3 -m unittest discover -s handoff -t . -p 'test_*.py' -v"""
import json
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
        self.assertEqual(set(cfg.untaught("hand")), {"ready", "offer", "carry"})
        self.assertFalse(cfg.gripper.contact_calibrated)

    def test_live_runner_refuses_untaught_poses(self):
        from handoff import run
        self.assertEqual(run.main(["--mode", "mark", "--feed", "operator", "--operator-cutoff"]), 2)

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
