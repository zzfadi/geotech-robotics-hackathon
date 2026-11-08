"""Station JSON lines in, voice cues out. Standard library only.

Reads the JSON the station already prints and turns it into short event keys:

- handoff routine events (``python3 -m handoff.replay --trace`` on stdout, or the
  indented ``{"kind": ...}`` lines ``python3 -m handoff.run`` prints on stderr):
  ``step:offer:1``, ``contact:grasp``, ``end:failed:no_grasp``, ...
- turn gate events (``python3 -m turn_gate.replay`` records, or bare gate
  events): ``gate:robot_turn_granted``, ``gate:robot_turn_revoked:hand_present``
- vision frames (``python3 -m vision.run``): one key per moment change, such as
  ``moment:can_released``; frames with no change produce nothing
- handoff trial rows (``handoff.run`` stdout): ``trial:success:held``

Each key is looked up in lines.json. The voice only announces: nothing in this
package sends anything to the arm, the routine or the turn gate.
"""
from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

HERE = Path(__file__).resolve().parent
LINES = HERE / "lines.json"
LOCAL = HERE / "local"
AUDIO = LOCAL / "audio"

_MODES = {
    "Take the can from a person's hand": "hand",
    "Pick the can from the taped mark": "mark",
    "Reset to the ready pose": "reset",
}
_GATE_KINDS = ("holding", "turn_requested", "robot_turn_granted", "robot_turn_revoked",
               "robot_turn_completed")


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")


@dataclass(frozen=True)
class Event:
    source: str            # handoff | gate | vision | trial
    key: str               # e.g. "step:offer:1"
    data: dict = field(default_factory=dict, compare=False)
    t: Optional[float] = None   # the source's own time stamp, if it has one


@dataclass(frozen=True)
class Line:
    id: str
    text: str
    voice: str
    urgent: bool = False


def parse_json_line(text: str) -> Optional[dict]:
    """The JSON object on this line, or None. Tolerates leading spaces and prefixes."""
    s = text.strip()
    i = s.find("{")
    if i < 0:
        return None
    try:
        rec = json.loads(s[i:])
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


def _num(x) -> Optional[float]:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _handoff(rec: dict) -> List[Event]:
    kind, t = rec.get("kind"), _num(rec.get("t"))
    if kind == "task":
        mode = _MODES.get(rec.get("name"), slug(rec.get("name") or "task"))
        if rec.get("phase") == "end":
            key = "end:%s:%s" % (rec.get("outcome") or "unknown", rec.get("detail") or "unknown")
            if mode == "reset":
                key = "task:reset_end:%s" % (rec.get("outcome") or "unknown")
        else:
            key = "task:%s:%s" % (rec.get("phase") or "start", mode)
        return [Event("handoff", key, rec, t)]
    if kind == "step":
        step, detail = rec.get("step") or "unknown", str(rec.get("detail") or "")
        key = "step:" + step
        m = re.match(r"attempt (\d+)$", detail)
        if m:
            key += ":" + m.group(1)
        elif detail == "resumed":
            key += ":resumed"
        elif step == "paused" and detail:
            key += ":" + slug(detail)
        return [Event("handoff", key, rec, t)]
    if kind == "decision":
        return [Event("handoff", "decision:" + slug(rec.get("what") or ""), rec, t)]
    if kind == "contact":
        return [Event("handoff", "contact:" + slug(rec.get("what") or "unknown"), rec, t)]
    if kind == "gate":
        return [Event("handoff", "handoff_gate:%s:%s" % (rec.get("event"), rec.get("reason")), rec, t)]
    if kind == "state":
        return [Event("handoff", "state", rec, t)]
    if kind == "arm_calls":
        return [Event("handoff", "arm_calls", rec, t)]
    return []


def _gate_event(e: dict) -> Event:
    return Event("gate", "gate:%s:%s" % (e.get("kind"), e.get("reason")), e, _num(e.get("at")))


def normalize(rec: dict) -> List[Event]:
    """Every event key one station record carries (often none)."""
    if not isinstance(rec, dict):
        return []
    if rec.get("type") == "frame" and isinstance(rec.get("moment"), dict):
        out = []
        for m in rec["moment"].get("events") or []:
            out.append(Event("vision", "moment:" + str(m.get("kind")), m, _num(m.get("at"))))
        for a in rec.get("actions") or []:
            out.append(Event("vision", "vision_action:" + str(a.get("action")), a,
                             _num(rec["moment"].get("captured_at"))))
        for g in rec.get("gate_events") or []:
            out.append(Event("vision", "vision_gate:%s:%s" % (g.get("kind"), g.get("reason")), g,
                             _num(g.get("at"))))
        return out
    if "type" in rec and "kind" not in rec:
        return []   # vision session summaries and other records with nothing to say
    if "kind" in rec and rec["kind"] in _GATE_KINDS and "state" in rec:
        return [_gate_event(rec)]
    if "kind" in rec:
        return _handoff(rec)
    if isinstance(rec.get("events"), list) and "action" in rec:
        return [_gate_event(e) for e in rec["events"] if isinstance(e, dict)]
    if "trial" in rec and "outcome" in rec and "mode" in rec:
        return [Event("trial", "trial:%s:%s" % (rec["outcome"], rec.get("reason")), rec)]
    return []


class Lines:
    """lines.json: which line, in which voice, for each event key."""

    def __init__(self, doc: dict):
        self.doc = doc
        self.voices: Dict[str, dict] = doc.get("voices", {})
        self.rules: dict = dict({"same_line_cooldown_s": 8.0, "min_gap_s": 0.4, "max_wait_s": 3.0,
                                 "max_queue": 2}, **doc.get("rules", {}))
        self.by_id: Dict[str, Line] = {}
        self._exact: Dict[str, str] = {}
        self._patterns: List[Tuple[str, str]] = []
        for item in doc.get("lines", []):
            line = Line(item["id"], item["text"], item["voice"], bool(item.get("urgent")))
            if line.id in self.by_id:
                raise ValueError("duplicate line id %r" % line.id)
            if line.voice not in self.voices:
                raise ValueError("line %r uses unknown voice %r" % (line.id, line.voice))
            self.by_id[line.id] = line
            for key in item.get("on", []):
                if any(c in key for c in "*?["):
                    self._patterns.append((key, line.id))
                elif self._exact.setdefault(key, line.id) != line.id:
                    raise ValueError("key %r is mapped twice" % key)
        self.silent = set(doc.get("silent", []))

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Lines":
        return cls(json.loads(Path(path or LINES).read_text()))

    def resolve(self, key: str) -> Tuple[str, Optional[Line]]:
        """('line', Line), ('silent', None) or ('unknown', None)."""
        parts = key.split(":")
        for n in range(len(parts), 0, -1):
            cand = ":".join(parts[:n])
            if cand in self._exact:
                return "line", self.by_id[self._exact[cand]]
            for pat, lid in self._patterns:
                if fnmatch.fnmatchcase(cand, pat):
                    return "line", self.by_id[lid]
            if cand in self.silent:
                return "silent", None
        return "unknown", None


class Cuer:
    """Decides which events speak: de-duplicated and rate-limited.

    The same line never repeats within ``same_line_cooldown_s`` (two sources
    reporting one moment speak once). At most ``max_queue`` lines wait; an
    urgent line always gets through and drops the waiting ones.
    """

    def __init__(self, lines: Lines):
        self.lines = lines
        self.last_said: Dict[str, float] = {}
        self.why = ""   # why the last event stayed quiet: silent, unknown, repeat, busy

    def cue(self, event: Event, now: float, pending: int = 0) -> Optional[Line]:
        status, line = self.lines.resolve(event.key)
        if line is None:
            self.why = status
            return None
        cool = float(self.lines.rules["same_line_cooldown_s"])
        last = self.last_said.get(line.id)
        if last is not None and now - last < cool:
            self.why = "repeat"
            return None
        if not line.urgent and pending >= int(self.lines.rules["max_queue"]):
            self.why = "busy"
            return None
        self.last_said[line.id] = now
        self.why = ""
        return line


def events_from_lines(lines: Iterable[str]) -> Iterable[Event]:
    for text in lines:
        rec = parse_json_line(text)
        if rec is not None:
            yield from normalize(rec)
