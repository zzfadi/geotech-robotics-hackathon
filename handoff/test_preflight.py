"""python3 -m unittest discover -s handoff -t . -p 'test_*.py' -v

Preflight tests. Every command goes through a fake runner except the last
two tests, which run the real module (git, lsof/ps and an import probe only).
"""
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from handoff import preflight
from handoff.preflight import MARK_ONLY, NO_GO, OK, WARN, Check

ROOT = Path(__file__).resolve().parents[1]
POSES = ROOT / "handoff" / "poses.json"


class FakeRunner:
    """Answers commands by their first matching prefix; records every call."""

    def __init__(self, answers):
        self.answers = answers
        self.calls = []

    def __call__(self, cmd, cwd=None, timeout=60.0, env=None):
        cmd = list(cmd)
        self.calls.append(cmd)
        for prefix, answer in self.answers:
            if cmd[:len(prefix)] == list(prefix):
                return answer
        return None, "%s not found" % cmd[0]


def git_answers(ahead, behind, origin=True):
    return [
        (["git", "rev-parse", "--short", "HEAD"], (0, "abc1234\n")),
        (["git", "fetch"], (0, "")),
        (["git", "rev-parse", "--verify"], (0 if origin else 1, "")),
        (["git", "rev-list"], (0, "%d\t%d\n" % (ahead, behind))),
    ]


def taught_poses(tmp, hand=True, mark=True, calibrated=True):
    raw = json.loads(POSES.read_text())
    hand_names = {"offer"}
    mark_names = {"above_mark", "at_mark"}
    for name, pose in raw["poses"].items():
        if name in hand_names:
            pose["taught"] = hand
        elif name in mark_names:
            pose["taught"] = mark
        else:
            pose["taught"] = True
    raw["gripper"]["contact_calibrated"] = calibrated
    path = Path(tmp) / "poses.json"
    path.write_text(json.dumps(raw))
    return path


DETECTOR_OK = ([sys.executable, "-c"], (0, '{"cv2": "4.10.0", "ultralytics": "8.3.0"}\n'))
DETECTOR_MISSING = ([sys.executable, "-c"], (1, "ModuleNotFoundError: No module named 'cv2'\n"))
LSOF_PICKUP = [(["lsof"], (0, "p4242\ncPython\n")),
               (["ps"], (0, "arm/.venv/bin/python arm/tools/pickup_server.py right --operator-cutoff\n"))]


class VerdictTest(unittest.TestCase):
    def test_worst_level_wins(self):
        self.assertEqual(preflight.verdict([Check("a", OK, ""), Check("b", WARN, "")]), "GO")
        self.assertEqual(preflight.verdict([Check("a", OK, ""), Check("b", MARK_ONLY, "")]), MARK_ONLY)
        self.assertEqual(preflight.verdict([Check("a", MARK_ONLY, ""), Check("b", NO_GO, "")]), NO_GO)


class GitTest(unittest.TestCase):
    def test_up_to_date_without_fetching(self):
        r = FakeRunner(git_answers(0, 0))
        c = preflight.check_git(ROOT, fetch=False, runner=r)
        self.assertEqual(c.level, OK)
        self.assertIn("matches origin/main", c.reason)
        self.assertFalse([cmd for cmd in r.calls if "fetch" in cmd], "fetched without --fetch")

    def test_fetch_only_when_asked(self):
        r = FakeRunner(git_answers(0, 0))
        preflight.check_git(ROOT, fetch=True, runner=r)
        self.assertIn(["git", "fetch", "--quiet", "origin", "main"], r.calls)

    def test_behind_is_no_go_with_the_pull_command(self):
        c = preflight.check_git(ROOT, runner=FakeRunner(git_answers(0, 3)))
        self.assertEqual(c.level, NO_GO)
        self.assertIn("3 commits behind", c.reason)
        self.assertIn("git pull --ff-only origin main", c.reason)

    def test_ahead_warns_and_diverged_is_no_go(self):
        self.assertEqual(preflight.check_git(ROOT, runner=FakeRunner(git_answers(2, 0))).level, WARN)
        self.assertEqual(preflight.check_git(ROOT, runner=FakeRunner(git_answers(1, 1))).level, NO_GO)

    def test_offline_or_no_origin_only_warns(self):
        self.assertEqual(preflight.check_git(ROOT, runner=FakeRunner(git_answers(0, 0, origin=False))).level, WARN)
        self.assertEqual(preflight.check_git(ROOT, runner=FakeRunner([])).level, WARN)
        failed_fetch = git_answers(0, 0)
        failed_fetch[1] = (["git", "fetch"], (128, "fatal: unable to access origin\n"))
        c = preflight.check_git(ROOT, fetch=True, runner=FakeRunner(failed_fetch))
        self.assertEqual(c.level, OK)
        self.assertIn("fetch failed", c.reason)


class PortTest(unittest.TestCase):
    def never_bind(self, host, port):
        raise AssertionError("bind test should not run when the owner is known")

    def test_held_by_pickup_server_is_ok(self):
        c = preflight.check_port(8765, runner=FakeRunner(LSOF_PICKUP), bind=self.never_bind)
        self.assertEqual(c.level, OK)
        self.assertIn("pickup_server", c.reason)
        self.assertIn("4242", c.reason)

    def test_held_by_anything_else_is_no_go(self):
        r = FakeRunner([(["lsof"], (0, "p77\ncPython\n")),
                        (["ps"], (0, "python3 -m control_center serve --demo\n"))])
        c = preflight.check_port(8765, runner=r, bind=self.never_bind)
        self.assertEqual(c.level, NO_GO)
        self.assertIn("control_center", c.reason)
        self.assertIn("77", c.reason)

    def test_long_command_lines_still_name_the_script(self):
        py = "/Applications/Xcode.app/Contents/Developer/Library/Frameworks/Python3.framework/Versions/3.9/Resources/Python.app/Contents/MacOS/Python"
        for script, level, shown in (("arm/tools/pickup_server.py right --operator-cutoff", OK, "pickup_server.py"),
                                     ("-m control_center serve --port 8765", NO_GO, "control_center serve")):
            r = FakeRunner([(["lsof"], (0, "p55\ncPython\n")), (["ps"], (0, "%s %s\n" % (py, script)))])
            c = preflight.check_port(8765, runner=r, bind=self.never_bind)
            self.assertEqual(c.level, level)
            self.assertIn(shown, c.reason)
            self.assertIn("pid 55", c.reason)

    def test_free_port(self):
        r = FakeRunner([(["lsof"], (1, ""))])
        c = preflight.check_port(8765, runner=r, bind=lambda h, p: True)
        self.assertEqual(c.level, OK)
        self.assertIn("free", c.reason)

    def test_in_use_by_an_unseen_owner_warns(self):
        c = preflight.check_port(8765, runner=FakeRunner([(["lsof"], (1, ""))]), bind=lambda h, p: False)
        self.assertEqual(c.level, WARN)

    def test_ss_fallback_on_linux(self):
        r = FakeRunner([(["ss"], (0, 'LISTEN 0 5 127.0.0.1:8765 0.0.0.0:* users:(("python",pid=901,fd=3))\n')),
                        (["ps"], (0, "python arm/tools/pickup_server.py right --operator-cutoff --log x\n"))])
        c = preflight.check_port(8765, runner=r, bind=self.never_bind)
        self.assertEqual(c.level, OK)
        self.assertIn("901", c.reason)

    def test_a_real_listener_on_a_spare_port_is_not_ok(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        try:
            c = preflight.check_port(s.getsockname()[1])
        finally:
            s.close()
        # lsof sees this test process (not pickup_server): NO-GO; without lsof: WARN.
        self.assertIn(c.level, (NO_GO, WARN), c)


class VenvAndPosesTest(unittest.TestCase):
    def test_arm_venv(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(preflight.check_arm_venv(Path(tmp), env={}).level, NO_GO)
            py = Path(tmp) / "arm" / ".venv" / "bin" / "python"
            py.parent.mkdir(parents=True)
            py.write_text("#!/bin/sh\n")
            py.chmod(0o755)
            self.assertEqual(preflight.check_arm_venv(Path(tmp), env={}).level, OK)
            self.assertEqual(preflight.check_arm_venv(Path(tmp), env={"ARMCTL_PYTHON": "/nope/python"}).level,
                             NO_GO)

    def levels(self, path):
        return {c.name: c.level for c in preflight.check_poses(path)}

    def test_untaught_poses_as_committed(self):
        lv = self.levels(POSES)
        self.assertEqual(lv["mark poses"], NO_GO)
        self.assertEqual(lv["hand poses"], MARK_ONLY)

    def test_taught_poses(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.levels(taught_poses(tmp)),
                             {"mark poses": OK, "hand poses": OK, "grip band": OK})
            lv = self.levels(taught_poses(tmp, hand=False, calibrated=False))
            self.assertEqual((lv["mark poses"], lv["hand poses"], lv["grip band"]), (OK, MARK_ONLY, WARN))

    def test_broken_poses_file_is_no_go(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "poses.json"
            bad.write_text("{not json")
            self.assertEqual(self.levels(bad), {"poses": NO_GO})


class DetectorTest(unittest.TestCase):
    def test_missing_detector_means_mark_only(self):
        checks = preflight.check_detector(sys.executable, runner=FakeRunner([DETECTOR_MISSING]))
        self.assertEqual([c.level for c in checks], [MARK_ONLY])
        self.assertIn("cv2", checks[0].reason)

    def test_detector_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            for w in ("a.pt", "b.pt"):
                (Path(tmp) / w).write_text("")
            checks = preflight.check_detector(sys.executable, ("a.pt", "b.pt"),
                                              runner=FakeRunner([DETECTOR_OK]), cwd=Path(tmp))
            self.assertEqual([c.level for c in checks], [OK])
            self.assertIn("ultralytics 8.3.0", checks[0].reason)
            checks = preflight.check_detector(sys.executable, ("a.pt", "missing.pt"),
                                              runner=FakeRunner([DETECTOR_OK]), cwd=Path(tmp))
            self.assertEqual([c.level for c in checks], [OK, WARN])
            self.assertIn("missing.pt", checks[1].reason)


class TestsCheckTest(unittest.TestCase):
    PASS = [
        (["bash", "arm/tools/arm", "test"], (0, "........\n140 passed in 0.48s\n")),
        ([sys.executable, "-m", "unittest"], (0, "Ran 33 tests in 2.0s\n\nOK\n")),
        ([sys.executable, "-m", "handoff.replay"], (0, '{"scenarios": 18, "passed": 18, "failed": 0}\n')),
        ([sys.executable, "-m", "handoff.teach"], (0, "ready       TEACH [1]\noffer       ok    [2]\n"
                                                      "contact band -60 to -12, NOT MEASURED\n")),
    ]

    def test_all_pass(self):
        checks = preflight.check_tests(ROOT, FakeRunner(self.PASS))
        self.assertEqual([c.level for c in checks], [OK] * 4)
        self.assertIn("140 passed", checks[0].reason)
        self.assertIn("18 of 18", checks[2].reason)
        self.assertIn("1 pose still to teach", checks[3].reason)

    def test_a_failure_is_no_go(self):
        answers = list(self.PASS)
        answers[2] = ([sys.executable, "-m", "handoff.replay"], (1, '{"scenarios": 18, "passed": 17, "failed": 1}\n'))
        answers[0] = (["bash", "arm/tools/arm", "test"], (1, "1 failed, 139 passed\n"))
        levels = [c.level for c in preflight.check_tests(ROOT, FakeRunner(answers))]
        self.assertEqual(levels, [NO_GO, OK, NO_GO, OK])


class MainTest(unittest.TestCase):
    def run_main(self, args, answers):
        r = FakeRunner(answers)
        out = io.StringIO()
        with redirect_stdout(out):
            code = preflight.main(args, runner=r)
        return code, out.getvalue(), r

    def test_go_mark_only_and_no_go_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            poses = str(taught_poses(tmp))
            base = git_answers(0, 0) + LSOF_PICKUP
            # Weights named by absolute path so the weights check passes.
            for w in ("d.pt", "p.pt"):
                (Path(tmp) / w).write_text("")
            args = ["--poses", poses, "--detect", str(Path(tmp) / "d.pt"), "--pose", str(Path(tmp) / "p.pt")]
            code, out, r = self.run_main(args, base + [DETECTOR_OK])
            self.assertEqual(code, 0, out)
            self.assertIn("GO: hand mode and mark mode", out)
            code, out, _ = self.run_main(args, base + [DETECTOR_MISSING])
            self.assertEqual(code, 3, out)
            self.assertIn("MARK-ONLY", out.splitlines()[-1])
            code, out, _ = self.run_main(args, git_answers(0, 2) + LSOF_PICKUP + [DETECTOR_OK])
            self.assertEqual(code, 1, out)
            self.assertIn("NO-GO: fix git first", out)

    def test_only_harmless_commands_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = ["--poses", str(taught_poses(tmp)), "--json"]
            code, out, r = self.run_main(args, git_answers(0, 0) + LSOF_PICKUP + [DETECTOR_OK])
        data = json.loads(out)
        self.assertEqual(data["verdict"], "GO")
        allowed = (["git", "rev-parse"], ["git", "rev-list"], ["lsof"], ["ps"], [sys.executable, "-c"])
        for cmd in r.calls:
            self.assertTrue(any(cmd[:len(a)] == a for a in allowed), cmd)
        probe = [cmd for cmd in r.calls if cmd[:2] == [sys.executable, "-c"]]
        self.assertEqual(probe[0][2], preflight.DETECTOR_PROBE)
        for word in ("serial", "usb", "can0", "VideoCapture", "connect"):
            self.assertNotIn(word, preflight.DETECTOR_PROBE)


class RealRunTest(unittest.TestCase):
    def test_module_runs_offline_and_reports_json(self):
        # A spare port, so a test run never even test-binds the arm server's 8765.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        p = subprocess.run([sys.executable, "-m", "handoff.preflight", "--json", "--port", str(port)], cwd=str(ROOT),
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True, timeout=180)
        data = json.loads(p.stdout)
        self.assertIn(data["verdict"], ("GO", "MARK-ONLY", "NO-GO"))
        self.assertEqual(p.returncode, preflight.EXIT[data["verdict"]])
        names = [c["check"] for c in data["checks"]]
        for name in ("git", "port %d" % port, "arm venv", "mark poses", "hand poses", "detector"):
            self.assertIn(name, names)


if __name__ == "__main__":
    unittest.main()
