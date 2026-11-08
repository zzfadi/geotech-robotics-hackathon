"""Detector-agnostic boxes, plus converters from YOLO pose and MediaPipe hands.

Boxes are normalized image coordinates (x_min, y_min, x_max, y_max) in [0, 1].
A frame's ``captured_at`` is the capture time in the turn gate's monotonic clock
domain, never the inference-finished or receipt time (see turn_gate/README.md).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence, Tuple

Box = Tuple[float, float, float, float]

# COCO-17 keypoint indices used by Ultralytics pose models.
LEFT_ELBOW, RIGHT_ELBOW, LEFT_WRIST, RIGHT_WRIST = 7, 8, 9, 10


def finite(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


@dataclass(frozen=True)
class Detection:
    label: str
    confidence: float
    box: Box

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label:
            raise ValueError("detection label must be a non-empty string")
        if not finite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("detection confidence must be in [0, 1]")
        if not isinstance(self.box, (tuple, list)) or len(self.box) != 4:
            raise ValueError("detection box needs four values")
        if not all(finite(v) and 0 <= v <= 1 for v in self.box):
            raise ValueError("detection box must be normalized to [0, 1]")
        x0, y0, x1, y1 = self.box
        if x0 > x1 or y0 > y1:
            raise ValueError("detection box corners are reversed")
        object.__setattr__(self, "box", tuple(float(v) for v in self.box))

    @property
    def center(self) -> Tuple[float, float]:
        x0, y0, x1, y1 = self.box
        return (x0 + x1) / 2, (y0 + y1) / 2

    @classmethod
    def from_record(cls, record: dict) -> "Detection":
        return cls(record["label"], record.get("conf", record.get("confidence", 1.0)),
                   tuple(record["box"]))


@dataclass(frozen=True)
class FrameDetections:
    """All detections from one captured frame. ``valid=False`` means detector failure.

    An empty, valid detection list means the detector ran and saw nothing. A
    failed or skipped inference must be reported as invalid, never as empty.
    """

    captured_at: float
    detections: Tuple[Detection, ...] = ()
    valid: bool = True
    error: str = ""
    source: str = field(default="", compare=False)

    @classmethod
    def from_record(cls, record: dict) -> "FrameDetections":
        return cls(
            record["captured_at"],
            tuple(Detection.from_record(d) for d in record.get("detections", ())),
            record.get("valid", True) is True,
            record.get("error", ""),
            record.get("source", ""),
        )


def box_around(cx: float, cy: float, size: float) -> Box:
    half = size / 2
    return (clamp01(cx - half), clamp01(cy - half), clamp01(cx + half), clamp01(cy + half))


def overlaps(a: Box, b: Box, *, margin: float = 0.0) -> bool:
    """True when boxes touch, after growing ``a`` by ``margin`` on every side."""
    return not (
        a[2] + margin < b[0] or a[0] - margin > b[2]
        or a[3] + margin < b[1] or a[1] - margin > b[3]
    )


def hands_from_pose(
    people: Iterable[Sequence[Sequence[float]]],
    *,
    min_keypoint_conf: float = 0.5,
    hand_box_size: float = 0.1,
    reach: float = 0.35,
) -> Tuple[Detection, ...]:
    """Turn COCO-17 pose keypoints into ``hand`` boxes. COCO has no hand class.

    Each person is 17 rows of (x, y, conf) normalized to the frame. The hand is
    placed past the wrist, ``reach`` times the forearm length along the elbow to
    wrist direction, because the wrist keypoint sits behind the palm holding the
    can. The box size is a fixed fraction of the frame and wants tuning on the
    real table (inferred, not measured).
    """
    hands = []
    for person in people:
        if len(person) != 17:
            raise ValueError("pose needs 17 COCO keypoints per person")
        for elbow_i, wrist_i in ((LEFT_ELBOW, LEFT_WRIST), (RIGHT_ELBOW, RIGHT_WRIST)):
            wx, wy, wc = (float(v) for v in person[wrist_i][:3])
            if not (finite(wc) and wc >= min_keypoint_conf and finite(wx) and finite(wy)):
                continue
            if not (0 <= wx <= 1 and 0 <= wy <= 1):
                continue
            ex, ey, ec = (float(v) for v in person[elbow_i][:3])
            cx, cy = wx, wy
            if finite(ec) and ec >= min_keypoint_conf and finite(ex) and finite(ey):
                cx, cy = wx + reach * (wx - ex), wy + reach * (wy - ey)
            hands.append(Detection("hand", clamp01(wc), box_around(clamp01(cx), clamp01(cy), hand_box_size)))
    return tuple(hands)


def hands_from_landmarks(hands: Iterable[Sequence[Sequence[float]]], *, confidence: float = 1.0) -> Tuple[Detection, ...]:
    """MediaPipe 21-landmark hands (as ``turn_gate.camera`` returns them) to boxes."""
    out = []
    for hand in hands:
        if len(hand) != 21:
            raise ValueError("MediaPipe hands have 21 landmarks")
        xs = [clamp01(float(p[0])) for p in hand]
        ys = [clamp01(float(p[1])) for p in hand]
        out.append(Detection("hand", confidence, (min(xs), min(ys), max(xs), max(ys))))
    return tuple(out)
