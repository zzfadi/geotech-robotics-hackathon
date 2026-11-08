"""Turn detector output into the three facts the handoff needs.

The detector (YOLO on the Orin Nano, or anything else) sends one JSON object
per camera frame. Two shapes are accepted.

Boxes, one per frame and camera::

    {"captured_at": 12.345, "camera": "wrist_r", "valid": true,
     "detections": [{"label": "can", "box": [x0, y0, x1, y1], "conf": 0.91},
                    {"label": "hand", "box": [...], "conf": 0.88}]}

Flags, when the detector already decided::

    {"captured_at": 12.345, "valid": true,
     "hand_in_path": false, "can_in_jaws": true, "hand_on_can": true}

``captured_at`` is the frame's capture time on the routine's monotonic clock
(time.monotonic() on the same host), never receipt or inference time. Boxes
are normalized to [0, 1]. A failed inference is ``"valid": false``, never an
empty detection list: an empty list means "looked and saw nothing".

Facts:
  hand_in_path  any hand box touches the path zone (the space the arm sweeps
                between its base and the offer pose). Feeds the turn gate.
  can_in_jaws   the best can box is centred in the jaw zone and its width is in
                the expected band (it is close, not across the room).
  hand_on_can   a hand box overlaps the can box: the person is still holding it.
"""
from __future__ import annotations

import math
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

Box = Tuple[float, float, float, float]


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _box(value) -> Optional[Box]:
    if not isinstance(value, (list, tuple)) or len(value) != 4 or not all(map(_num, value)):
        return None
    x0, y0, x1, y1 = (float(v) for v in value)
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        return None
    return (x0, y0, x1, y1)


def _intersects(a: Box, b: Box) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _overlap_fraction(of: Box, by: Box) -> float:
    w = min(of[2], by[2]) - max(of[0], by[0])
    h = min(of[3], by[3]) - max(of[1], by[1])
    if w <= 0 or h <= 0:
        return 0.0
    return (w * h) / ((of[2] - of[0]) * (of[3] - of[1]))


@dataclass(frozen=True)
class Zones:
    path_camera: str = "head"
    path: Box = (0.0, 0.0, 1.0, 1.0)
    jaw_camera: str = "wrist_r"
    jaws: Box = (0.3, 0.3, 0.7, 0.8)
    can_min_width: float = 0.2
    can_max_width: float = 0.85
    hand_on_can_overlap: float = 0.05
    min_conf: float = 0.4

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "Zones":
        if not d:
            return cls()
        kw = dict(d)
        for key in ("path", "jaws"):
            if key in kw:
                b = _box(kw[key])
                if b is None:
                    raise ValueError("zone %s must be x0 y0 x1 y1 inside [0, 1]" % key)
                kw[key] = b
        return cls(**kw)


@dataclass(frozen=True)
class Scene:
    """One fused look at the handoff. None means unknown, never 'no'."""

    captured_at: float
    valid: bool
    hand_in_path: Optional[bool] = None
    can_in_jaws: Optional[bool] = None
    hand_on_can: Optional[bool] = None
    can_offset: Optional[Tuple[float, float]] = None  # can centre minus jaw centre
    reason: str = ""

    @property
    def presenting(self) -> bool:
        return self.valid and self.can_in_jaws is True and self.hand_on_can is True

    @property
    def released(self) -> bool:
        return self.valid and self.hand_on_can is False


def _labelled(dets, label: str, min_conf: float) -> List[Tuple[float, Box]]:
    out = []
    for d in dets:
        if not isinstance(d, dict) or d.get("label") != label:
            continue
        conf = d.get("conf", 1.0)
        b = _box(d.get("box"))
        if b is not None and _num(conf) and conf >= min_conf:
            out.append((float(conf), b))
    return out


def facts_from_boxes(dets, zones: Zones, *, path: bool, jaws: bool) -> Dict[str, object]:
    """Facts from one camera frame's detections. Only the roles asked for."""
    if not isinstance(dets, (list, tuple)):
        raise ValueError("detections must be a list")
    hands = [b for _, b in _labelled(dets, "hand", zones.min_conf)]
    out: Dict[str, object] = {}
    if path:
        out["hand_in_path"] = any(_intersects(b, zones.path) for b in hands)
    if jaws:
        cans = sorted(_labelled(dets, "can", zones.min_conf), reverse=True)
        if not cans:
            out.update(can_in_jaws=False, hand_on_can=False, can_offset=None)
        else:
            c = cans[0][1]
            cx, cy = (c[0] + c[2]) / 2, (c[1] + c[3]) / 2
            j = zones.jaws
            width = c[2] - c[0]
            centred = j[0] <= cx <= j[2] and j[1] <= cy <= j[3]
            out["can_in_jaws"] = centred and zones.can_min_width <= width <= zones.can_max_width
            out["hand_on_can"] = any(
                _overlap_fraction(c, h) >= zones.hand_on_can_overlap for h in hands)
            out["can_offset"] = (round(cx - (j[0] + j[2]) / 2, 4),
                                 round(cy - (j[1] + j[3]) / 2, 4))
    return out


@dataclass
class _Latest:
    captured_at: float
    valid: bool
    facts: Dict[str, object] = field(default_factory=dict)
    reason: str = ""


class Fuser:
    """Keeps the newest frame per role and fuses them into a Scene.

    A scene is only valid when every role it needs has a valid frame no older
    than ``stale_after_s``; its capture time is the oldest of those frames, so a
    stalled camera cannot look fresh behind a live one.
    """

    def __init__(self, zones: Zones = Zones(), stale_after_s: float = 0.25):
        self.zones = zones
        self.stale_after_s = stale_after_s
        self._latest: Dict[str, _Latest] = {}
        self._high_water: Dict[str, float] = {}
        self._history: Dict[str, deque] = {}
        self._lock = threading.Lock()

    def _store(self, role: str, entry: _Latest) -> None:
        prev = self._high_water.get(role)
        if prev is not None and entry.captured_at <= prev:
            return  # repeated or out-of-order frame: never refreshes freshness
        self._high_water[role] = entry.captured_at
        self._latest[role] = entry
        self._history.setdefault(role, deque(maxlen=500)).append(entry)

    def history(self, role: str, after: Optional[float]) -> List[Scene]:
        """Every stored frame for one role captured after ``after``, oldest first."""
        with self._lock:
            entries = list(self._history.get(role, ()))
        out = []
        for e in entries:
            if after is not None and e.captured_at <= after:
                continue
            if not e.valid:
                out.append(Scene(e.captured_at, False, reason=e.reason or "detector_invalid"))
            else:
                out.append(Scene(e.captured_at, True, e.facts.get("hand_in_path"),
                                 e.facts.get("can_in_jaws"), e.facts.get("hand_on_can"),
                                 e.facts.get("can_offset")))
        return out

    def push(self, msg: dict) -> None:
        with self._lock:
            self._push(msg)

    def _push(self, msg: dict) -> None:
        if not isinstance(msg, dict) or not _num(msg.get("captured_at")):
            return
        t = float(msg["captured_at"])
        valid = msg.get("valid", True) is True
        if "detections" in msg:
            cam = msg.get("camera")
            path = cam == self.zones.path_camera
            jaws = cam == self.zones.jaw_camera
            if not (path or jaws):
                return
            facts: Dict[str, object] = {}
            reason = ""
            if valid:
                try:
                    facts = facts_from_boxes(msg["detections"], self.zones, path=path, jaws=jaws)
                except ValueError:
                    valid, reason = False, "bad_detections"
            else:
                reason = "detector_invalid"
            for role, wanted in (("path", path), ("jaws", jaws)):
                if wanted:
                    self._store(role, _Latest(t, valid, facts, reason))
            return
        # Flags message: one source that already decided all three.
        facts = {k: msg[k] for k in ("hand_in_path", "can_in_jaws", "hand_on_can", "can_offset")
                 if k in msg}
        for k in ("hand_in_path", "can_in_jaws", "hand_on_can"):
            if k in facts and type(facts[k]) is not bool:
                valid = False
        for role, key in (("path", "hand_in_path"), ("jaws", "can_in_jaws")):
            if key in facts or not valid:
                self._store(role, _Latest(t, valid, facts, "" if valid else "detector_invalid"))

    def scene(self, now: float, roles: Sequence[str] = ("path", "jaws")) -> Optional[Scene]:
        with self._lock:
            entries = [self._latest.get(r) for r in roles]
        if any(e is None for e in entries):
            return None
        captured = min(e.captured_at for e in entries)
        valid, reason = True, ""
        for e in entries:
            if not e.valid:
                valid, reason = False, e.reason or "detector_invalid"
            elif now - e.captured_at > self.stale_after_s:
                valid, reason = False, "stale"
        merged: Dict[str, object] = {}
        for r, e in zip(roles, entries):
            keys = ("hand_in_path",) if r == "path" else ("can_in_jaws", "hand_on_can", "can_offset")
            for k in keys:
                if k in e.facts:
                    merged[k] = e.facts[k]
        if not valid:
            return Scene(captured, False, reason=reason)
        return Scene(captured, True, merged.get("hand_in_path"), merged.get("can_in_jaws"),
                     merged.get("hand_on_can"), merged.get("can_offset"))
