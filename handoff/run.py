"""Run the handoff (or the taped-mark fallback) on the real arm, one trial at a time.

Needs pickup_server.py running for the arm (localhost:8765) with the station
operator on the e-stop, and a detector feed on the same host's monotonic clock:

  # terminal 1: the guarded arm server (holds until 'rest')
  PYTHONPATH=arm/tools arm/.venv/bin/python arm/tools/pickup_server.py right \\
      --operator-cutoff --log arm/local/handoff/server.jsonl
  # terminal 2: detector piped into the routine
  python3 -m vision.run --camera 0 --detect yolo26n.engine --pose yolo26n-pose.engine \\
      --target real --verbose --max-seconds 3600 \\
    | python3 -m handoff.run --mode hand --feed - --operator-cutoff --trials 10

Fallback with no detector (operator says when the path is clear):

  python3 -m handoff.run --mode mark --feed operator --operator-cutoff --trials 10

Rehearse everything without the arm (fake arm on the real clock):

  python3 -m handoff.run --mode hand --feed - --dry-run ...

Keys, typed in this terminal then Enter: g = go (hold my beer) or resume after a
pause, s = stop now, h = toggle "hand in path" (operator feed only), q = quit.
Every trial is appended to arm/local/handoff/trials.jsonl and written as a
control center session. Tally with: python3 -m handoff.trials
"""
from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .arm_link import FakeArm, RealClock, SocketArm
from .config import load
from .detections import Fuser, Zones
from .replay import to_telemetry
from .routine import Routine

ROOT = Path(__file__).resolve().parents[1]
TRIALS = ROOT / "arm" / "local" / "handoff" / "trials.jsonl"


class KeyOperator:
    def __init__(self, keys: "queue.Queue[str]"):
        self.keys = keys

    def poll(self):
        out = []
        while True:
            try:
                k = self.keys.get_nowait()
            except queue.Empty:
                return out
            out.append({"g": "go", "": "go", "r": "resume", "s": "stop", "q": "stop"}.get(k, k))


def read_keys(stream, keys: "queue.Queue[str]", operator_feed: dict) -> None:
    for line in stream:
        k = line.strip().lower()[:1]
        if k == "h":
            operator_feed["hand"] = not operator_feed["hand"]
            print("operator feed: hand in path = %s" % operator_feed["hand"], file=sys.stderr)
            continue
        keys.put(k)


def follow(stream, fuser: Fuser) -> None:
    for line in stream:
        try:
            fuser.push(json.loads(line))
        except ValueError:
            continue
    # Feed ended: the next fused scene goes stale and every motion holds.


def operator_feed(fuser: Fuser, state: dict, clock: RealClock) -> None:
    while not state.get("quit"):
        fuser.push({"captured_at": clock.now(), "valid": True, "hand_in_path": state["hand"]})
        time.sleep(0.05)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--mode", choices=("hand", "mark"), required=True)
    p.add_argument("--feed", required=True, help="'-' for stdin, a file to follow, or 'operator'")
    p.add_argument("--operator-cutoff", action="store_true", required=True,
                   help="confirms the station operator is on the e-stop")
    p.add_argument("--poses", type=Path, default=None)
    p.add_argument("--zones", type=Path, default=None, help="JSON zones for raw box feeds")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--trials", type=int, default=1)
    p.add_argument("--dry-run", action="store_true", help="fake arm on the real clock; no server needed")
    p.add_argument("--telemetry", default=str(ROOT / "control_center" / "local" / "sessions"))
    p.add_argument("--trial-log", type=Path, default=TRIALS)
    p.add_argument("--note", default="", help="who held the can, which can, anything odd")
    a = p.parse_args(argv)

    cfg = load(a.poses)
    untaught = cfg.untaught(a.mode)
    if untaught and not a.dry_run:
        print("Not moving the real arm: these poses are not taught yet: %s.\n"
              "Teach each one at the station with python3 -m handoff.teach NAME." % ", ".join(untaught),
              file=sys.stderr)
        return 2
    if a.mode == "hand" and a.feed == "operator":
        print("Hand mode needs a detector feed: it waits for the can between the jaws.", file=sys.stderr)
        return 2
    if not cfg.gripper.contact_calibrated:
        print("warning: the gripper contact band is not measured on the can yet "
              "(python3 -m handoff.teach --contact)", file=sys.stderr)

    clock = RealClock()
    zones = Zones.from_dict(json.loads(a.zones.read_text())) if a.zones else Zones()
    fuser = Fuser(zones, stale_after_s=cfg.timing.stale_after_s)
    feed_state = {"hand": False}
    if a.feed == "operator":
        threading.Thread(target=operator_feed, args=(fuser, feed_state, clock), daemon=True).start()
        key_stream = sys.stdin
    else:
        stream = sys.stdin if a.feed == "-" else open(a.feed, "r")
        threading.Thread(target=follow, args=(stream, fuser), daemon=True).start()
        key_stream = open("/dev/tty", "r") if a.feed == "-" else sys.stdin
    keys: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=read_keys, args=(key_stream, keys, feed_state), daemon=True).start()
    operator = KeyOperator(keys)

    if a.dry_run:
        first = cfg.chain(cfg.hand_approach if a.mode == "hand" else cfg.mark_approach)[0]
        jaws = lambda t: bool((fuser.scene(t, ("jaws",)) or None) and fuser.scene(t, ("jaws",)).can_in_jaws)
        arm = FakeArm(clock, first.q, can_between_jaws=jaws if a.mode == "hand" else (lambda t: True))
    else:
        arm = SocketArm(a.port)

    sys.path.insert(0, str(ROOT))
    from control_center.telemetry import Writer

    a.trial_log.parent.mkdir(parents=True, exist_ok=True)
    tally = []
    for n in range(1, a.trials + 1):
        print("\nTrial %d of %d (%s). Type g + Enter to start, s to stop, q to quit." % (
            n, a.trials, a.mode), file=sys.stderr)
        robot = "maker-arm-%s%s" % (cfg.arm, " (dry run)" if a.dry_run else "")
        with Writer(a.telemetry, component="handoff") as w:
            w.emit("session", phase="start", robot=robot, purpose="sim" if a.dry_run else "live",
                   note="%s trial %d" % (a.mode, n))
            routine = Routine(cfg, arm, fuser, operator, clock, mode=a.mode,
                              emit=lambda kind, f: (to_telemetry(w, a.mode, kind, f),
                                                    print("  " + json.dumps(dict(kind=kind, **f)),
                                                          file=sys.stderr)))
            result = routine.run()
            w.emit("session", phase="end", robot=robot, purpose="sim" if a.dry_run else "live")
            session = w.session
        row = {"when": datetime.now(timezone.utc).isoformat(), "mode": a.mode, "trial": n,
               "dry_run": a.dry_run, "session": session, "note": a.note, **result.summary()}
        with a.trial_log.open("a") as fh:
            fh.write(json.dumps(row) + "\n")
        tally.append(result.outcome == "success")
        print(json.dumps(row))
        print("Result: %s (%s). %d of %d so far." % (result.outcome, result.reason, sum(tally), len(tally)),
              file=sys.stderr)
        if result.reason == "operator_stop" or n == a.trials:
            break
        print("Take the can from the jaws if it is held, then g + Enter to open and return to ready.",
              file=sys.stderr)
        reset = Routine(cfg, arm, fuser, operator, clock, mode=a.mode)
        reset._acquire(cfg.timing.trigger_timeout_s, True, "reset for the next trial")
        reset.reset_to_ready()
    feed_state["quit"] = True
    print("Arm left enabled and holding. Rest it with: arm/.venv/bin/python arm/tools/pickup_client.py rest",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
