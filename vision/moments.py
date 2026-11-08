"""Turn per-frame hand and can boxes into handoff moments and a motion verdict.

The turn gate (``turn_gate``) only lets the robot move while no hand is present,
which suits token placement but not "hold my beer", where the robot has to
reach toward a hand. This tracker decides *which* hand presence is acceptable:

* no hand in the keep-out zone, or
* exactly one hand holding a can in the handoff zone, steady before the turn
  starts and without a gross jerk once it is engaged, or
* a can left behind after the hand let go.

Anything else (an empty hand, two hands, a can snatched away, a detector
failure) reports ``motion_ok=False`` or invalid, which the coordinator feeds to
the turn gate as ``hand_present=True`` / ``valid=False`` so the gate revokes.

This is software arbitration, not a safety system. Thresholds are first guesses
for a webcam at arm's length and have not been tuned on the real table.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple

from .detections import Box, Detection, FrameDetections, finite, overlaps

STATES = ("EMPTY", "HAND", "OFFERING", "ENGAGED", "RELEASED", "INVALID")

# Moment names emitted on state changes, keyed by (before, after).
_MOMENTS = {
    "HAND": "hand_entered",
    "OFFERING": "offer_started",
    "ENGAGED": "offer_steady",
    "RELEASED": "can_released",
    "INVALID": "observation_invalid",
}


@dataclass(frozen=True)
class Zone:
    """Inclusive normalized rectangle; touching an edge counts as inside."""

    x_min: float = 0.0
    y_min: float = 0.0
    x_max: float = 1.0
    y_max: float = 1.0

    def __post_init__(self) -> None:
        values = (self.x_min, self.y_min, self.x_max, self.y_max)
        if not all(finite(v) and 0 <= v <= 1 for v in values):
            raise ValueError("zone coordinates must be in [0, 1]")
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("zone needs positive width and height")

    @property
    def box(self) -> Box:
        return (self.x_min, self.y_min, self.x_max, self.y_max)

    def touches(self, box: Box) -> bool:
        return overlaps(self.box, box)

    def contains_point(self, x: float, y: float) -> bool:
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max


@dataclass(frozen=True)
class MomentConfig:
    hand_labels: Tuple[str, ...] = ("hand",)
    # COCO-80 has no "can"; a can usually comes back as "bottle" or "cup".
    can_labels: Tuple[str, ...] = ("can", "bottle", "cup")
    min_confidence: float = 0.35
    keep_out: Zone = Zone()                       # where any hand matters
    handoff: Zone = Zone(0.3, 0.2, 0.7, 0.8)      # where the can is offered
    grip_margin: float = 0.03                     # hand-can overlap slack
    steady_seconds: float = 0.4                   # held still before the reach
    steady_speed: float = 0.08                    # frame widths/s while waiting
    engaged_speed: float = 0.6                    # jerk limit once engaged
    release_seconds: float = 0.3                  # hand off the can this long
    occlusion_seconds: float = 0.3                # can hidden by the gripper
    max_frame_gap: float = 0.25                   # longer gaps reset motion

    def __post_init__(self) -> None:
        for name in ("min_confidence", "grip_margin", "steady_seconds", "steady_speed",
                     "engaged_speed", "release_seconds", "occlusion_seconds", "max_frame_gap"):
            value = getattr(self, name)
            if not finite(value) or value < 0:
                raise ValueError(name + " must be finite and non-negative")
        if not self.min_confidence <= 1:
            raise ValueError("min_confidence must be at most 1")


@dataclass(frozen=True)
class MomentEvent:
    kind: str
    at: float
    state: str
    reason: str


@dataclass(frozen=True)
class MomentUpdate:
    """One frame's verdict. ``motion_ok`` is None when the frame was unusable."""

    captured_at: float
    state: str
    reason: str
    motion_ok: Optional[bool]
    valid: bool
    events: Tuple[MomentEvent, ...]
    can_center: Optional[Tuple[float, float]] = None
    can_speed: Optional[float] = None


class MomentTracker:
    """Feed frames in capture order. The tracker keeps only the last can track."""

    def __init__(self, config: MomentConfig = MomentConfig()) -> None:
        self.config = config
        self.state = "EMPTY"
        self.reason = "awaiting_frames"
        self._last_at: Optional[float] = None
        self._can_at: Optional[Tuple[float, float]] = None
        self._steady_since: Optional[float] = None
        self._release_since: Optional[float] = None
        self._missing_since: Optional[float] = None
        self._prev_center: Optional[Tuple[float, float]] = None

    def _labelled(self, frame: FrameDetections, labels) -> Tuple[Detection, ...]:
        floor = self.config.min_confidence
        return tuple(d for d in frame.detections if d.label in labels and d.confidence >= floor)

    def _reset_motion(self) -> None:
        self._can_at = None
        self._steady_since = None
        self._release_since = None
        self._missing_since = None

    def update(self, frame: FrameDetections) -> MomentUpdate:
        cfg = self.config
        at = frame.captured_at
        before = self.state
        speed = None
        center = None

        if not finite(at) or (self._last_at is not None and at <= self._last_at):
            self.state, self.reason = "INVALID", "non_increasing_capture_time"
            self._reset_motion()
            return self._emit(before, at if finite(at) else 0.0, None, False, None, None)
        gap = None if self._last_at is None else at - self._last_at
        self._last_at = at
        if gap is not None and gap > cfg.max_frame_gap:
            self._reset_motion()
        if not frame.valid:
            self.state, self.reason = "INVALID", frame.error or "detector_failed"
            self._reset_motion()
            return self._emit(before, at, None, False, None, None)

        hands = tuple(h for h in self._labelled(frame, cfg.hand_labels) if cfg.keep_out.touches(h.box))
        cans = self._labelled(frame, cfg.can_labels)
        held = [c for c in cans if any(overlaps(h.box, c.box, margin=cfg.grip_margin) for h in hands)]
        offered = [c for c in held if cfg.handoff.contains_point(*c.center)]
        track = max(offered or held or cans, key=lambda d: d.confidence, default=None)
        if track is not None:
            center = track.center
            if self._can_at is not None:
                prev_at, prev = self._can_at, self._prev_center
                dt = at - prev_at
                if dt > 0:
                    speed = math.hypot(center[0] - prev[0], center[1] - prev[1]) / dt
            self._can_at, self._prev_center = at, center
        else:
            self._can_at = None

        engaged_before = before in ("ENGAGED", "RELEASED")
        if track is not None or not engaged_before:
            self._missing_since = None
        if not hands:
            if track is not None and engaged_before:
                self.state, self.reason = "RELEASED", "can_without_hand"
            else:
                self.state, self.reason = "EMPTY", "no_hand_in_keep_out"
            self._steady_since = self._release_since = None
        elif len(hands) > 1:
            self.state, self.reason = "HAND", "multiple_hands"
            self._steady_since = self._release_since = None
        elif before == "RELEASED":
            if held:
                self.state, self.reason = "HAND", "hand_returned_to_can"
            else:
                self.state, self.reason = "RELEASED", "hand_let_go"
        elif before == "ENGAGED":
            if held:
                self._release_since = self._missing_since = None
                if speed is not None and speed > cfg.engaged_speed:
                    self.state, self.reason = "HAND", "can_jerked_away"
                else:
                    self.state, self.reason = "ENGAGED", "holding_offer"
            elif track is not None:
                # Hand still in view but off the can: count down the release.
                self._missing_since = None
                if self._release_since is None:
                    self._release_since = at
                if at - self._release_since >= cfg.release_seconds - 1e-6:
                    self.state, self.reason = "RELEASED", "hand_let_go"
                else:
                    self.state, self.reason = "ENGAGED", "release_pending"
            else:
                # The gripper often hides the can; allow a short occlusion.
                if self._missing_since is None:
                    self._missing_since = at
                if at - self._missing_since >= cfg.occlusion_seconds - 1e-6:
                    self.state, self.reason = "HAND", "can_lost"
                else:
                    self.state, self.reason = "ENGAGED", "can_occluded"
        elif offered:
            if speed is not None and speed > cfg.steady_speed:
                self.state, self.reason = "OFFERING", "can_moving"
                self._steady_since = None
            else:
                if self._steady_since is None:
                    self._steady_since = at
                if speed is not None and at - self._steady_since >= cfg.steady_seconds - 1e-6:
                    self.state, self.reason = "ENGAGED", "offer_steady"
                else:
                    self.state, self.reason = "OFFERING", "steadying"
        else:
            self.state = "HAND"
            self.reason = "hand_holding_can_outside_zone" if held else "empty_hand"
            self._steady_since = None

        if self.state == "HAND":
            self._steady_since = self._release_since = self._missing_since = None
        motion_ok = self.state in ("EMPTY", "ENGAGED", "RELEASED")
        return self._emit(before, at, motion_ok, True, center, speed)

    def _emit(self, before, at, motion_ok, valid, center, speed) -> MomentUpdate:
        events = ()
        if self.state != before:
            kind = _MOMENTS.get(self.state)
            if self.state == "EMPTY":
                kind = "hand_withdrawn" if before != "INVALID" else "observation_recovered"
            elif self.state == "HAND" and before in ("OFFERING", "ENGAGED"):
                kind = "offer_withdrawn"
            elif self.state == "HAND" and before == "RELEASED":
                kind = "hand_returned"
            if kind:
                events = (MomentEvent(kind, at, self.state, self.reason),)
        return MomentUpdate(at, self.state, self.reason, motion_ok, valid, events, center, speed)
