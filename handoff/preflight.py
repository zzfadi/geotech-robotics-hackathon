"""Station preflight: is this machine ready for the handoff trials?

  python3 -m handoff.preflight                 # quick, offline, about a second
  python3 -m handoff.preflight --run-tests     # also the arm tests, handoff tests, replay, teach --show
  python3 -m handoff.preflight --fetch         # refresh origin/main first (needs the network)
  python3 -m handoff.preflight --vision-python ~/venv/bin/python   # where vision.run will run

It prints one line per check and a verdict:

  GO         hand mode and mark mode can both run
  MARK-ONLY  the taped-mark fallback can run; hand mode cannot (no detector, or
             a hand-mode pose is not taught)
  NO-GO      fix the NO-GO lines first

Exit code 0 for GO, 3 for MARK-ONLY, 1 for NO-GO.

It never touches hardware: it opens no CAN, USB or camera device, sends
nothing to the arm server and never moves the arm. It reads git, poses.json
and the process list, test-binds 127.0.0.1:8765 for an instant when it cannot
see who holds it, and runs only commands that need no hardware.
Standard library only, Python 3.9 or newer.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

from .config import DEFAULT_POSES, load

ROOT = Path(__file__).resolve().parents[1]
ARM_PORT = 8765

OK, WARN, MARK_ONLY, NO_GO = "ok", "warn", "MARK-ONLY", "NO-GO"
GO = "GO"
EXIT = {GO: 0, MARK_ONLY: 3, NO_GO: 1}

# (returncode or None if it could not run, combined output)
Runner = Callable[..., Tuple[Optional[int], str]]


class Check:
    def __init__(self, name: str, level: str, reason: str):
        self.name, self.level, self.reason = name, level, reason

    def as_dict(self) -> dict:
        return {"check": self.name, "level": self.level, "reason": self.reason}

    def __repr__(self) -> str:
        return "Check(%r, %r, %r)" % (self.name, self.level, self.reason)


def run(cmd: Sequence[str], cwd: Optional[Path] = None, timeout: float = 60.0,
        env: Optional[dict] = None) -> Tuple[Optional[int], str]:
    try:
        p = subprocess.run(list(cmd), cwd=str(cwd) if cwd else None, timeout=timeout, env=env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, universal_newlines=True)
    except FileNotFoundError:
        return None, "%s not found" % cmd[0]
    except subprocess.TimeoutExpired:
        return None, "timed out after %.0f s" % timeout
    except OSError as exc:
        return None, str(exc)
    return p.returncode, p.stdout or ""


def verdict(checks: Sequence[Check]) -> str:
    levels = {c.level for c in checks}
    if NO_GO in levels:
        return NO_GO
    if MARK_ONLY in levels:
        return MARK_ONLY
    return GO


# ------------------------------------------------------------------- git
def check_git(root: Path = ROOT, fetch: bool = False, runner: Runner = run) -> Check:
    name = "git"
    rc, head = runner(["git", "rev-parse", "--short", "HEAD"], cwd=root, timeout=10)
    if rc != 0:
        return Check(name, WARN, "cannot read HEAD (%s)" % head.strip()[:80])
    head = head.strip()
    fetched = ""
    if fetch:
        frc, out = runner(["git", "fetch", "--quiet", "origin", "main"], cwd=root, timeout=30)
        fetched = "fetched" if frc == 0 else "fetch failed (%s), using the cached origin/main" % (
            out.strip().splitlines()[-1][:60] if out.strip() else "offline?")
    rc, _ = runner(["git", "rev-parse", "--verify", "--quiet", "origin/main"], cwd=root, timeout=10)
    if rc != 0:
        return Check(name, WARN, "HEAD %s; no origin/main to compare with" % head)
    rc, out = runner(["git", "rev-list", "--left-right", "--count", "HEAD...origin/main"], cwd=root, timeout=10)
    try:
        ahead, behind = (int(x) for x in out.split())
    except ValueError:
        return Check(name, WARN, "HEAD %s; could not compare with origin/main" % head)
    source = fetched or "cached origin/main; --fetch to refresh"
    if behind and ahead:
        return Check(name, NO_GO, "HEAD %s has diverged from origin/main (%d ahead, %d behind; %s): "
                                  "integrate on main without discarding work" % (head, ahead, behind, source))
    if behind:
        return Check(name, NO_GO, "HEAD %s is %d commit%s behind origin/main (%s): git pull --ff-only origin main"
                     % (head, behind, "" if behind == 1 else "s", source))
    if ahead:
        return Check(name, WARN, "HEAD %s is %d commit%s ahead of origin/main (%s): push when ready"
                     % (head, ahead, "" if ahead == 1 else "s", source))
    return Check(name, OK, "HEAD %s matches origin/main (%s)" % (head, source))


# ------------------------------------------------------------------ port
def _lsof_owners(port: int, runner: Runner) -> Optional[List[Tuple[int, str]]]:
    """[(pid, short name)] listening on the port, [] for none, None if lsof is missing."""
    rc, out = runner(["lsof", "-nP", "-iTCP:%d" % port, "-sTCP:LISTEN", "-Fpc"], timeout=10)
    if rc is None:
        return None
    owners, pid = [], None
    for line in out.splitlines():
        if line.startswith("p") and line[1:].isdigit():
            pid = int(line[1:])
        elif line.startswith("c") and pid is not None:
            owners.append((pid, line[1:]))
            pid = None
    return owners


def _ss_owners(port: int, runner: Runner) -> Optional[List[Tuple[int, str]]]:
    rc, out = runner(["ss", "-ltnpH", "sport = :%d" % port], timeout=10)
    if rc is None:
        return None
    owners = []
    for name, pid in re.findall(r'\("([^"]+)",pid=(\d+)', out):
        owners.append((int(pid), name))
    if not owners and out.strip():
        owners.append((0, "a process this user cannot see"))
    return owners


def _command_line(pid: int, runner: Runner) -> str:
    if pid <= 0:
        return ""
    rc, out = runner(["ps", "-o", "command=", "-p", str(pid)], timeout=10)
    return out.strip() if rc == 0 else ""


def can_bind(host: str, port: int) -> bool:
    """Bind the way pickup_server does (SO_REUSEADDR), then let go at once."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def check_port(port: int = ARM_PORT, host: str = "127.0.0.1", runner: Runner = run,
               bind: Callable[[str, int], bool] = can_bind) -> Check:
    name = "port %d" % port
    owners = _lsof_owners(port, runner)
    if owners is None:
        owners = _ss_owners(port, runner)
    if owners:
        described = []
        foreign = []
        for pid, short in owners:
            cmd = _command_line(pid, runner) or short
            # Keep the end of a long command line: the script name is there, not
            # in the interpreter path (macOS framework Pythons run past 90 chars).
            shown = cmd if len(cmd) <= 90 else "..." + cmd[-87:]
            described.append("%s (pid %d)" % (shown, pid) if pid else shown)
            if "pickup_server" not in cmd:
                foreign.append(described[-1])
        if not foreign:
            return Check(name, OK, "held by the arm server: %s" % "; ".join(described))
        return Check(name, NO_GO, "held by %s; the arm tools need %s:%d, stop it (the control center "
                                  "now uses 8780)" % ("; ".join(foreign), host, port))
    if bind(host, port):
        return Check(name, OK, "%s:%d is free for pickup_server" % (host, port))
    return Check(name, WARN, "%s:%d is in use but the owner is not visible (no lsof/ss, or another "
                             "user); if it is not pickup_server, the server will refuse to start" % (host, port))


# ------------------------------------------------------------- arm venv
def check_arm_venv(root: Path = ROOT, env: Optional[dict] = None) -> Check:
    env = os.environ if env is None else env
    override = env.get("ARMCTL_PYTHON")
    py = Path(override) if override else root / "arm" / ".venv" / "bin" / "python"
    label = "ARMCTL_PYTHON=%s" % override if override else "arm/.venv"
    if py.exists() and os.access(str(py), os.X_OK):
        return Check("arm venv", OK, "%s present (%s)" % (label, py))
    return Check("arm venv", NO_GO, "%s missing: pickup_server and the arm tests need it "
                                    "(follow arm/README.md)" % label)


# ---------------------------------------------------------------- poses
def check_poses(path: Path = DEFAULT_POSES) -> List[Check]:
    try:
        cfg = load(path)
    except (OSError, ValueError) as exc:
        return [Check("poses", NO_GO, "%s does not load: %s" % (path.name, exc))]
    out = []
    mark, hand = cfg.untaught("mark"), cfg.untaught("hand")
    if mark:
        out.append(Check("mark poses", NO_GO, "not taught yet: %s (python3 -m handoff.teach NAME); "
                                              "the live runner refuses to move" % ", ".join(mark)))
    else:
        out.append(Check("mark poses", OK, "taught: %s" % ", ".join(cfg.needed("mark"))))
    if hand:
        out.append(Check("hand poses", MARK_ONLY, "not taught yet: %s" % ", ".join(hand)))
    else:
        out.append(Check("hand poses", OK, "taught: %s" % ", ".join(cfg.needed("hand"))))
    g = cfg.gripper
    if g.contact_calibrated:
        out.append(Check("grip band", OK, "contact band %.1f to %.1f deg, measured on the can"
                         % (g.contact_min_deg, g.contact_max_deg)))
    else:
        out.append(Check("grip band", WARN, "contact band %.1f to %.1f deg is a wide default; measure it on "
                                            "the demo can (python3 -m handoff.teach --contact)"
                         % (g.contact_min_deg, g.contact_max_deg)))
    return out


# -------------------------------------------------------------- detector
DETECTOR_PROBE = ("import json, cv2, ultralytics; "
                  "print(json.dumps({'cv2': cv2.__version__, 'ultralytics': ultralytics.__version__}))")


def check_detector(python: str = sys.executable, weights: Sequence[str] = ("yolo26n.pt", "yolo26n-pose.pt"),
                   runner: Runner = run, cwd: Path = ROOT) -> List[Check]:
    rc, out = runner([python, "-c", DETECTOR_PROBE], cwd=cwd, timeout=120)
    versions = None
    for line in reversed(out.strip().splitlines()):
        try:
            versions = json.loads(line)
            break
        except ValueError:
            continue
    if rc != 0 or not isinstance(versions, dict):
        last = out.strip().splitlines()[-1] if out.strip() else "no output"
        return [Check("detector", MARK_ONLY, "cv2/ultralytics do not import in %s (%s): no detector, "
                                             "so hand mode cannot run" % (python, last[:100]))]
    out_checks = [Check("detector", OK, "cv2 %s and ultralytics %s import in %s"
                        % (versions.get("cv2"), versions.get("ultralytics"), python))]
    missing = [w for w in weights if w and not (Path(w).exists() or (cwd / w).exists())]
    if missing:
        out_checks.append(Check("weights", WARN, "%s not found here; ultralytics downloads missing .pt weights "
                                                 "on first use, which needs the internet" % ", ".join(missing)))
    return out_checks


# ----------------------------------------------------------------- tests
def check_tests(root: Path = ROOT, runner: Runner = run, python: str = sys.executable) -> List[Check]:
    out = []
    # The wrapper's fixed list leaves out the pickup_server startup tests; add them.
    rc, text = runner(["bash", "arm/tools/arm", "test", "arm/tools/test_pickup_server_startup.py"],
                      cwd=root, timeout=600)
    m = re.findall(r"(\d+) passed", text)
    if rc == 0:
        out.append(Check("arm tests", OK, "bash arm/tools/arm test (+ pickup_server startup): %s passed" % (m[-1] if m else "all")))
    else:
        out.append(Check("arm tests", NO_GO, "bash arm/tools/arm test failed: %s" % _tail(text)))
    rc, text = runner([python, "-m", "unittest", "discover", "-s", "handoff", "-t", ".", "-p", "test_*.py"],
                      cwd=root, timeout=600)
    m = re.findall(r"Ran (\d+) tests?", text)
    if rc == 0:
        out.append(Check("handoff tests", OK, "%s handoff unit tests pass" % (m[-1] if m else "all")))
    else:
        out.append(Check("handoff tests", NO_GO, "handoff unit tests failed: %s" % _tail(text)))
    rc, text = runner([python, "-m", "handoff.replay"], cwd=root, timeout=600)
    summary = None
    for line in reversed(text.strip().splitlines()):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if isinstance(d, dict) and "scenarios" in d:
            summary = d
            break
    if rc == 0 and summary:
        out.append(Check("replay", OK, "%d of %d scenarios pass" % (summary["passed"], summary["scenarios"])))
    else:
        out.append(Check("replay", NO_GO, "handoff.replay failed: %s" % (
            "%s of %s pass" % (summary["passed"], summary["scenarios"]) if summary else _tail(text))))
    rc, text = runner([python, "-m", "handoff.teach", "--show"], cwd=root, timeout=60)
    if rc == 0:
        todo = sum(1 for line in text.splitlines() if line.split()[1:2] == ["TEACH"])
        out.append(Check("teach --show", OK, "reads poses.json: %d pose%s still to teach"
                         % (todo, "" if todo == 1 else "s")))
    else:
        out.append(Check("teach --show", NO_GO, "handoff.teach --show failed: %s" % _tail(text)))
    return out


def _tail(text: str) -> str:
    lines = [l for l in text.strip().splitlines() if l.strip()]
    return lines[-1][:120] if lines else "no output"


# ------------------------------------------------------------------ main
def gather(a, runner: Runner = run) -> List[Check]:
    checks = [check_git(ROOT, a.fetch, runner), check_port(a.port, runner=runner), check_arm_venv(ROOT)]
    checks += check_poses(a.poses)
    checks += check_detector(a.vision_python, (a.detect, a.pose), runner)
    if a.run_tests:
        checks += check_tests(ROOT, runner)
    return checks


def report(checks: Sequence[Check], stream=None) -> str:
    stream = stream or sys.stdout
    v = verdict(checks)
    print("Handoff station preflight (no hardware touched)", file=stream)
    for c in checks:
        print("  %-9s %-14s %s" % (c.level, c.name, c.reason), file=stream)
    first = next((c for c in checks if c.level == v), None)
    if v == GO:
        print("GO: hand mode and mark mode can both run.", file=stream)
    elif v == MARK_ONLY:
        print("MARK-ONLY: the taped-mark fallback can run; hand mode cannot (%s: %s)."
              % (first.name, first.reason), file=stream)
    else:
        bad = [c.name for c in checks if c.level == NO_GO]
        print("NO-GO: fix %s first." % ", ".join(bad), file=stream)
    return v


def main(argv=None, runner: Runner = run) -> int:
    p = argparse.ArgumentParser(prog="python3 -m handoff.preflight", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--fetch", action="store_true", help="git fetch origin main first (network)")
    p.add_argument("--run-tests", action="store_true",
                   help="also run the arm tests, the handoff tests, handoff.replay and handoff.teach --show")
    p.add_argument("--vision-python", default=sys.executable,
                   help="the interpreter that will run vision.run for hand mode (default: this one)")
    p.add_argument("--detect", default="yolo26n.pt", help="detector weights hand mode will use")
    p.add_argument("--pose", default="yolo26n-pose.pt", help="pose weights hand mode will use")
    p.add_argument("--poses", type=Path, default=DEFAULT_POSES)
    p.add_argument("--port", type=int, default=ARM_PORT, help="the arm server's port")
    p.add_argument("--json", action="store_true", help="print the checks and verdict as JSON instead")
    a = p.parse_args(argv)
    checks = gather(a, runner)
    if a.json:
        v = verdict(checks)
        print(json.dumps({"verdict": v, "checks": [c.as_dict() for c in checks]}, indent=2))
    else:
        v = report(checks)
    return EXIT[v]


if __name__ == "__main__":
    sys.exit(main())
