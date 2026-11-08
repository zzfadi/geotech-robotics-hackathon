"""Replay handoff scenarios against the fake arm, on simulated time.

  python3 -m handoff.replay                       # every scenario in handoff/scenarios/
  python3 -m handoff.replay handoff/scenarios/hand_ok.json --trace
  python3 -m handoff.replay --telemetry control_center/local/sessions

A scenario scripts the operator, what the detector reports over time and how
the fake arm behaves, then states the expected outcome. Nothing here opens a
device or a network connection. A pass is a software result, not hardware
evidence.

Scenario timeline segments run from ``from`` to ``to`` seconds; keys a segment
leaves out carry over from the one before. ``valid: false`` makes the detector
report failure; ``missing: true`` sends no frames at all. ``truth_can`` is
whether a can is really between the jaws when they close (defaults to what the
detector says), so a detector false positive can be scripted. ``hand_visible``
(default true) is whether the jaw camera sees the person's hand at all; set it
false to script a hand hidden behind the can or a covered lens.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .arm_link import FakeArm, FakeClock
from .config import Config, load
from .detections import Fuser, Zones
from .routine import Result, Routine, ScriptedOperator

SCENARIOS = Path(__file__).with_name("scenarios")
FLAG_KEYS = ("hand_in_path", "can_in_jaws", "hand_on_can", "hand_visible")


def _segments(timeline: List[dict]) -> List[dict]:
    state = {"hand_in_path": False, "can_in_jaws": False, "hand_on_can": False,
             "hand_visible": True, "valid": True, "missing": False, "truth_can": None}
    out = []
    for seg in timeline:
        state = dict(state, **{k: v for k, v in seg.items() if k not in ("from", "to")})
        if "truth_can" not in seg:
            state["truth_can"] = None
        out.append(dict(state, start=float(seg["from"]), end=float(seg["to"])))
    return out


def _at(segments: List[dict], t: float) -> Optional[dict]:
    for s in segments:
        if s["start"] <= t < s["end"]:
            return s
    return None


class ReplayFeed:
    """Pushes scripted detector frames into the fuser as simulated time passes."""

    def __init__(self, segments: List[dict], fuser: Fuser, clock: FakeClock, fps: float,
                 keys=FLAG_KEYS):
        self.segments, self.fuser, self.clock, self.keys = segments, fuser, clock, keys
        self.dt = 1.0 / fps
        self.next_t = segments[0]["start"] if segments else 0.0
        self.end = segments[-1]["end"] if segments else 0.0

    def pump(self) -> None:
        now = self.clock.now()
        while self.next_t <= now and self.next_t < self.end:
            s = _at(self.segments, self.next_t)
            if s and not s["missing"]:
                msg = {"captured_at": round(self.next_t, 4), "valid": bool(s["valid"])}
                if s["valid"]:
                    msg.update({k: s[k] for k in self.keys})
                self.fuser.push(msg)
            self.next_t += self.dt


def run_scenario(sc: dict, cfg: Optional[Config] = None, emit=None) -> Result:
    cfg = cfg or load(sc.get("poses") and Path(sc["poses"]))
    clock = FakeClock(limit=float(sc.get("limit_s", 300)))
    segments = _segments(sc["timeline"])
    fuser = Fuser(Zones(), stale_after_s=cfg.timing.stale_after_s)
    feed = ReplayFeed(segments, fuser, clock, float(sc.get("fps", 15)),
                      ("hand_in_path",) if sc.get("path_only") else FLAG_KEYS)

    def truth(t: float) -> bool:
        if "truth_can" in sc:
            return bool(sc["truth_can"])
        s = _at(segments, t)
        if s is None:
            return False
        return bool(s["can_in_jaws"]) if s["truth_can"] is None else bool(s["truth_can"])

    a = sc.get("arm", {})
    first = cfg.chain(cfg.hand_approach if sc.get("mode", "hand") == "hand" else cfg.mark_approach)[0]
    arm = FakeArm(clock, first.q, can_between_jaws=truth, contact_deg=a.get("contact_deg", -33.0),
                  temps_c=a.get("temps_c", (40.0, 41.0)), heat_c_per_s=a.get("heat_c_per_s", 0.025),
                  trip_at=a.get("trip_at"), slip_at=a.get("slip_at"))
    operator = ScriptedOperator(clock, sc.get("operator", []))
    routine = Routine(cfg, arm, fuser, operator, clock, mode=sc.get("mode", "hand"),
                      emit=emit, before_read=feed.pump)
    result = routine.run()
    result.events.append({"t": round(clock.now(), 3), "kind": "arm_calls",
                          "moves": sum(1 for c in arm.calls if c["cmd"] == "move"),
                          "grips": [c["deg"] for c in arm.calls if c["cmd"] == "grip"]})
    return result


def check(sc: dict, result: Result) -> List[str]:
    problems = []
    for key, want in sc.get("expect", {}).items():
        if key.startswith("max_") or key.startswith("min_"):
            continue
        got = getattr(result, key)
        if got != want:
            problems.append("%s: expected %r, got %r" % (key, want, got))
    exp = sc.get("expect", {})
    if "max_seconds" in exp and result.seconds > exp["max_seconds"]:
        problems.append("took %.1f s, over %.1f s" % (result.seconds, exp["max_seconds"]))
    if "min_pauses" in exp and result.pauses < exp["min_pauses"]:
        problems.append("paused %d times, expected at least %d" % (result.pauses, exp["min_pauses"]))
    return problems


def _telemetry_writer(root: str, name: str):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from control_center.telemetry import Writer
    return Writer(root, component="handoff-replay", session=None)


def to_telemetry(writer, mode: str, kind: str, f: dict) -> None:
    """Map one routine event onto geotech.telemetry/1."""
    if kind == "task":
        writer.emit("task", phase=f["phase"], name=f["name"], outcome=f.get("outcome"),
                    detail=f.get("detail"))
    elif kind == "step":
        writer.emit("task", phase="step", name="can handoff" if mode == "hand" else "can from mark",
                    step=f["step"], detail=f.get("detail") or None)
    elif kind == "decision":
        writer.emit("decision", by=f["by"], what=f["what"], why=f["why"])
    elif kind == "contact":
        writer.emit("contact", what=f["what"], object=f.get("object"), detail=f.get("detail"))
    elif kind == "state":
        writer.emit("state", q=f["q"], grip=f["grip"])
    elif kind == "gate" and f["event"] == "robot_turn_revoked":
        writer.emit("decision", by="safety", what="Turn gate revoked the arm's turn", why=f["reason"])


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("scenarios", nargs="*", type=Path)
    p.add_argument("--trace", action="store_true", help="print every routine event")
    p.add_argument("--telemetry", metavar="DIR", help="also write each run as a control center session")
    a = p.parse_args(argv)
    paths = a.scenarios or sorted(SCENARIOS.glob("*.json"))
    failures = 0
    rows = []
    for path in paths:
        sc = json.loads(Path(path).read_text())
        writer = None
        emit = None
        if a.telemetry:
            writer = _telemetry_writer(a.telemetry, path.stem)
            writer.emit("session", phase="start", robot="maker-arm-right (fake)", purpose="replay",
                        note="handoff scenario " + path.stem)
            emit = lambda kind, f, w=writer, m=sc.get("mode", "hand"): to_telemetry(w, m, kind, f)
        result = run_scenario(sc, emit=emit)
        if writer:
            writer.emit("session", phase="end", robot="maker-arm-right (fake)", purpose="replay")
            writer.close()
        problems = check(sc, result)
        failures += bool(problems)
        if a.trace:
            for ev in result.events:
                print(json.dumps(ev))
        rows.append({"scenario": path.stem, "pass": not problems, **result.summary(),
                     **({"problems": problems} if problems else {})})
        print(json.dumps(rows[-1]))
    print(json.dumps({"scenarios": len(rows), "passed": len(rows) - failures, "failed": failures}))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
