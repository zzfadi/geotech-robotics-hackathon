"""Dream latency probe: fake clients and a fake clock, no key, no network."""

import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from control_center import telemetry
from vision import dream_latency
from vision.dream_latency import MockDreamClient, VirtualClock, main, percentile, run_probe
from vision.hybrid import ExecutorRouter

CHUNK = {"q_l_chunk": [[0.0] * 7] * 48, "q_r_chunk": [[0.0] * 7] * 48}


class FakeClient:
    """Scripted round trips: a number is milliseconds, an exception is raised, a dict is returned."""

    def __init__(self, script, clock=None):
        self.script, self.clock = list(script), clock or VirtualClock()
        self.made = self.observations = 0
        self.closed = False

    def set_observation(self, frames, observation, **_):
        self.observations += 1

    def get_action(self):
        item = self.script[self.made]
        self.made += 1
        if isinstance(item, Exception):
            self.clock.sleep(0.2)
            raise item
        if isinstance(item, dict):
            return item
        self.clock.sleep(item / 1000.0)
        return CHUNK

    def close(self):
        self.closed = True


def probe(script, **kwargs):
    client = FakeClient(script)
    summary = run_probe(client, {}, {"prompt": "p"}, calls=len(script), clock=client.clock,
                        sleep=client.clock.sleep, **kwargs)
    return client, summary


def events_of(directory):
    (session,) = [p for p in Path(directory).iterdir() if p.is_dir()]
    events, _ = telemetry.read(str(session / "events.jsonl"))
    return events


class ProbeTests(unittest.TestCase):
    def test_on_time_network_keeps_dream(self):
        client, summary = probe([700, 720, 690, 710, 705])
        self.assertEqual(client.observations, 5)
        self.assertEqual(summary["verdict"]["executor"], "dream")
        self.assertEqual(summary["verdict"]["reason"], "dream_on_time")
        self.assertAlmostEqual(summary["rtt_ms"]["median"], 705.0, places=3)
        self.assertEqual((summary["late"], summary["failed"]), (0, 0))

    def test_slow_median_goes_local_and_counts_late_replies(self):
        _, summary = probe([900, 950, 1000, 700, 880])
        self.assertEqual(summary["verdict"]["executor"], "local")
        self.assertEqual(summary["verdict"]["reason"], "dream_slow")
        self.assertEqual(summary["late"], 4)
        self.assertAlmostEqual(summary["rtt_ms"]["p95"], 1000.0, places=3)

    def test_only_the_last_five_count(self):
        _, summary = probe([2000, 2000, 2000, 700, 700, 700, 700, 700])
        self.assertEqual(summary["verdict"]["executor"], "dream")
        self.assertEqual(summary["late"], 3)

    def test_two_failures_in_a_row_go_local(self):
        _, summary = probe([700, RuntimeError("boom"), RuntimeError("boom")])
        self.assertEqual(summary["failed"], 2)
        self.assertEqual(summary["verdict"]["reason"], "dream_failing")

    def test_malformed_chunk_is_a_failure(self):
        _, summary = probe([{"q_l_chunk": [[0.0] * 6]}, 700])
        self.assertEqual((summary["failed"], summary["received"]), (1, 1))

    def test_timeout_stops_the_probe(self):
        class Slow:
            def set_observation(self, *a, **k):
                pass

            def get_action(self):
                time.sleep(0.3)
                return CHUNK

        summary = run_probe(Slow(), {}, {}, calls=5, clock=time.monotonic, sleep=time.sleep,
                            call_timeout=0.05)
        self.assertEqual(summary["calls"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertTrue(summary["stopped_early"])

    def test_exactly_850_ms_is_on_time_for_both_the_late_count_and_the_router(self):
        _, summary = probe([850] * 6)
        self.assertEqual(summary["late"], 0)
        self.assertEqual(summary["verdict"]["reason"], "dream_on_time")
        _, summary = probe([851] * 5)
        self.assertEqual((summary["late"], summary["verdict"]["reason"]), (5, "dream_slow"))

    def test_percentile_is_nearest_rank(self):
        self.assertEqual(percentile(list(range(1, 21)), 0.95), 19)
        self.assertEqual(percentile([5.0], 0.95), 5.0)
        self.assertIsNone(percentile([], 0.5))

    def test_mock_client_is_reproducible_and_phased(self):
        def run():
            clock = VirtualClock()
            client = MockDreamClient([700, 1400], sigma=0.0, calls=4, clock=clock)
            out = []
            for _ in range(4):
                before = clock()
                client.get_action()
                out.append(round((clock() - before) * 1000))
            return out

        self.assertEqual(run(), [700, 700, 1400, 1400])
        self.assertEqual(run(), run())


class TelemetryTests(unittest.TestCase):
    def test_probe_session_validates_and_records_the_switch(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(["--mock", "--mock-median-ms", "700,1300", "--mock-sigma", "0",
                             "--max-calls", "10", "--telemetry", tmp])
            self.assertEqual(code, 0)
            summary = json.loads(out.getvalue())
            self.assertEqual(summary["verdict"]["executor"], "local")
            self.assertIn("MOCK", summary["note"])
            events = events_of(tmp)
            self.assertEqual([p for e in events for p in telemetry.validate(e)], [])
            kinds = [e["kind"] for e in events]
            self.assertEqual(kinds[0], "session")
            self.assertEqual(events[0]["purpose"], "demo")
            self.assertEqual(events[-1]["phase"], "end")
            self.assertEqual(sum(1 for e in events if e["kind"] == "network" and e["phase"] == "received"), 10)
            self.assertEqual(sum(1 for e in events if e["kind"] == "network" and e["phase"] == "late"), 5)
            modes = [(e.get("previous"), e["mode"]) for e in events if e["kind"] == "mode"]
            self.assertEqual(modes, [(None, "async"), ("async", "sync")])
            loop = [e["what"] for e in events if e["kind"] == "decision" and e["by"] == "loop"]
            self.assertIn("Next handoff turn goes to the local controller", loop)
            self.assertEqual(loop[-1], "Verdict for this network: local")
            ts = [e["ts"] for e in events]
            self.assertEqual(ts, sorted(ts))


class RealModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state = root / "state.json"
        self.state.write_text(json.dumps({"q_left": [0.0] * 6, "q_right": [0.1] * 6,
                                          "gap_left_m": 0.05, "gap_right_m": 0.0}))
        self.images = []
        for name in ("o.jpg", "l.jpg", "r.jpg"):
            (root / name).write_bytes(b"not decoded in this test")
            self.images.append(str(root / name))
        self.args = ["--state", str(self.state), "--overhead", self.images[0], "--left", self.images[1],
                     "--right", self.images[2], "--interval", "0"]

    def run_main(self, argv, environ, make_client):
        err, out = io.StringIO(), io.StringIO()
        fake = ({"video.overhead": "a", "video.wrist_l": "b", "video.wrist_r": "c"},
                {"overhead": b"a", "wrist_l": b"b", "wrist_r": b"c"})
        with mock.patch.object(dream_latency, "encode_frames", return_value=fake), \
                redirect_stderr(err), redirect_stdout(out):
            code = main(argv, environ=environ, make_client=make_client)
        return code, out.getvalue(), err.getvalue()

    def test_refuses_without_a_key_and_never_builds_a_client(self):
        built = []
        code, out, err = self.run_main(self.args, {}, lambda *a: built.append(a))
        self.assertEqual(code, 2)
        self.assertIn("DREAM_API_KEY is not set", err)
        self.assertEqual(built, [])
        self.assertEqual(out, "")

    def test_refuses_without_an_observation(self):
        code, _, err = self.run_main(["--max-calls", "3"], {"DREAM_API_KEY": "k"}, lambda *a: None)
        self.assertEqual(code, 2)
        self.assertIn("captured observation", err)

    def test_hard_cap(self):
        code, _, err = self.run_main(self.args + ["--max-calls", "51"], {"DREAM_API_KEY": "k"}, lambda *a: None)
        self.assertEqual(code, 2)
        self.assertIn("--max-calls", err)

    def test_runs_the_capped_number_of_calls_and_logs_frames(self):
        client = FakeClient([1] * 20)
        with mock.patch.dict(os.environ, {"DREAM_API_KEY": "sekret-value"}):
            code, out, err = self.run_main(
                self.args + ["--max-calls", "3", "--telemetry", self.tmp.name + "/sessions"],
                {"DREAM_API_KEY": "sekret-value"}, lambda *a: client)
        self.assertEqual(code, 0)
        self.assertIn("COST WARNING", err)
        self.assertNotIn("sekret-value", out + err)
        self.assertEqual(client.made, 3)
        self.assertTrue(client.closed)
        summary = json.loads(out)
        self.assertEqual(summary["calls"], 3)
        events = events_of(self.tmp.name + "/sessions")
        self.assertEqual([p for e in events for p in telemetry.validate(e)], [])
        self.assertEqual(sorted(e["role"] for e in events if e["kind"] == "frame"), ["head", "wrist_l", "wrist_r"])
        self.assertEqual(events[0]["purpose"], "live")

    def test_bad_numbers_refuse_before_any_client(self):
        built = []
        for extra in (["--interval", "-1"], ["--call-timeout", "0"], ["--late-ms", "0"], ["--interval", "nan"]):
            code, out, err = self.run_main(self.args + extra, {"DREAM_API_KEY": "k"}, lambda *a: built.append(a))
            self.assertEqual((code, out), (2, ""), extra)
            self.assertIn("refusing", err)
        self.assertEqual(built, [])
        for extra in (["--mock-fail-rate", "2"], ["--mock-median-ms", "-5"], ["--mock-sigma", "-1"]):
            code, out, err = self.run_main(["--mock"] + extra, {}, None)
            self.assertEqual((code, out), (2, ""), extra)
            self.assertIn("refusing", err)

    def test_state_file_is_checked(self):
        self.state.write_text(json.dumps({"q_left": [0.0] * 5, "q_right": [0.0] * 6,
                                          "gap_left_m": 0.0, "gap_right_m": 0.0}))
        code, _, err = self.run_main(self.args, {"DREAM_API_KEY": "k"}, lambda *a: None)
        self.assertEqual(code, 2)
        self.assertIn("q_left", err)


if __name__ == "__main__":
    unittest.main()
