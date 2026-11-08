"""Poses and thresholds for the handoff and the taped-mark fallback.

Joint values are the SDK degrees that arm/tools/pickup_server.py takes (joints
1-6). Gripper values are SDK gripper degrees: about -96 is open, -2.5 is the
closed command, and the SDK stops closing and regulates 0.5 Nm on contact.

Every pose carries ``taught``. A pose copied from Saturday's cylinder run or
guessed is ``taught: false``; the live runner refuses to move the real arm
until each pose it needs was recorded at the station (handoff.teach).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

DEFAULT_POSES = Path(__file__).with_name("poses.json")


@dataclass(frozen=True)
class Pose:
    name: str
    q: Tuple[float, float, float, float, float, float]
    speed: float = 12.0
    taught: bool = False
    note: str = ""

    @classmethod
    def from_dict(cls, name: str, d: dict) -> "Pose":
        q = d.get("q")
        if not isinstance(q, (list, tuple)) or len(q) != 6:
            raise ValueError("pose %s needs six joint degrees" % name)
        if not all(isinstance(x, (int, float)) and not isinstance(x, bool)
                   and math.isfinite(x) for x in q):
            raise ValueError("pose %s has a non-finite joint" % name)
        speed = float(d.get("speed", 12.0))
        if not 1.0 <= speed <= 15.0:
            raise ValueError("pose %s speed must be 1-15 deg/s (server cap)" % name)
        return cls(name, tuple(float(x) for x in q), speed,
                   d.get("taught") is True, str(d.get("note", "")))


@dataclass(frozen=True)
class Gripper:
    open_deg: float = -90.0
    close_deg: float = -2.5
    close_wait_s: float = 3.0
    open_wait_s: float = 2.0
    # Where the jaws stop on a can. Wide until measured on the real can.
    contact_min_deg: float = -60.0
    contact_max_deg: float = -12.0
    # At or above this the jaws closed on nothing.
    empty_deg: float = -8.0
    # After the lift, more closure than this since contact means it slipped.
    slip_deg: float = 6.0
    contact_calibrated: bool = False
    contact_note: str = ""


@dataclass(frozen=True)
class Timing:
    segment_deg: float = 3.0          # longest joint step per guarded move segment
    trigger_timeout_s: float = 120.0  # operator never said "hold my beer"
    clear_timeout_s: float = 15.0     # path never cleared for a turn
    resume_timeout_s: float = 30.0    # paused move never resumed
    present_timeout_s: float = 20.0   # can never presented in the jaws
    present_debounce_s: float = 0.4   # can in jaws + hand on can, continuously
    release_timeout_s: float = 10.0   # person never let go
    release_debounce_s: float = 0.5   # hand off the can, continuously
    hold_check_s: float = 1.0         # dwell before the final hold check
    status_every_s: float = 1.0       # status poll while waiting at the offer pose
    clear_for_s: float = 0.5          # turn gate debounce
    stale_after_s: float = 0.25       # turn gate freshness


@dataclass(frozen=True)
class Config:
    arm: str = "right"
    poses: Dict[str, Pose] = field(default_factory=dict)
    # Pose name lists, in order.
    hand_approach: Tuple[str, ...] = ("ready", "offer")
    hand_retreat: Tuple[str, ...] = ("carry",)
    mark_approach: Tuple[str, ...] = ("ready", "above_mark")
    mark_descend: Tuple[str, ...] = ("at_mark",)
    mark_lift: Tuple[str, ...] = ("above_mark",)
    mark_retreat: Tuple[str, ...] = ("carry",)
    gripper: Gripper = Gripper()
    timing: Timing = Timing()
    max_grasp_attempts: int = 2
    temp_abort_c: float = 49.0        # pickup_server trips at 52 C
    resume_needs_operator: bool = True
    # Mark mode: if the detector feed is valid and says no can between the
    # jaws at the grasp pose, don't close.
    mark_check_can_before_close: bool = True

    def chain(self, names: Sequence[str]) -> List[Pose]:
        missing = [n for n in names if n not in self.poses]
        if missing:
            raise ValueError("poses not defined: " + ", ".join(missing))
        return [self.poses[n] for n in names]

    def needed(self, mode: str) -> List[str]:
        if mode == "hand":
            names = list(self.hand_approach) + list(self.hand_retreat)
        elif mode == "mark":
            names = (list(self.mark_approach) + list(self.mark_descend)
                     + list(self.mark_lift) + list(self.mark_retreat))
        else:
            raise ValueError("mode must be hand or mark")
        seen: List[str] = []
        for n in names:
            if n not in seen:
                seen.append(n)
        return seen

    def untaught(self, mode: str) -> List[str]:
        return [n for n in self.needed(mode)
                if n not in self.poses or not self.poses[n].taught]


def _dataclass_from(cls, d: Optional[dict]):
    if not d:
        return cls()
    names = {f.name for f in fields(cls)}
    unknown = set(d) - names
    if unknown:
        raise ValueError("unknown %s keys: %s" % (cls.__name__, ", ".join(sorted(unknown))))
    return replace(cls(), **d)


def load(path: Optional[Path] = None) -> Config:
    path = Path(path or DEFAULT_POSES)
    raw = json.loads(path.read_text())
    poses = {name: Pose.from_dict(name, d) for name, d in raw.get("poses", {}).items()}
    kwargs = {"poses": poses, "gripper": _dataclass_from(Gripper, raw.get("gripper")),
              "timing": _dataclass_from(Timing, raw.get("timing"))}
    for key in ("arm", "max_grasp_attempts", "temp_abort_c", "resume_needs_operator",
                "mark_check_can_before_close"):
        if key in raw:
            kwargs[key] = raw[key]
    for key in ("hand_approach", "hand_retreat", "mark_approach", "mark_descend",
                "mark_lift", "mark_retreat"):
        if key in raw:
            kwargs[key] = tuple(raw[key])
    cfg = Config(**kwargs)
    for mode in ("hand", "mark"):
        cfg.chain(cfg.needed(mode))  # every referenced pose exists
    g = cfg.gripper
    if not (g.open_deg < g.contact_min_deg < g.contact_max_deg < g.empty_deg <= g.close_deg):
        raise ValueError("gripper degrees must run open < contact band < empty <= close")
    return cfg
