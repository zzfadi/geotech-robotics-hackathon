"""Convert normalized hand landmarks to conservative image-space occupancy.

This tests 2D landmark bounds, not physical clearance. The caller must separately
establish source health, capture freshness and an appropriate camera workspace.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Tuple


Bounds = Tuple[float, float, float, float]


def _finite_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except (OverflowError, TypeError):
        return False


@dataclass(frozen=True)
class Workspace:
    """Inclusive normalized image rectangle; defaults to the entire frame."""

    x_min: float = 0.0
    y_min: float = 0.0
    x_max: float = 1.0
    y_max: float = 1.0

    def __post_init__(self) -> None:
        values = (self.x_min, self.y_min, self.x_max, self.y_max)
        if not all(_finite_number(value) and 0 <= value <= 1 for value in values):
            raise ValueError("workspace coordinates must be finite numbers in [0, 1]")
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError("workspace must have positive width and height")

    @classmethod
    def from_values(cls, values: object) -> Workspace:
        if not isinstance(values, (list, tuple)) or len(values) != 4:
            raise ValueError("workspace needs x_min, y_min, x_max, y_max")
        return cls(*values)

    def intersects(self, bounds: Bounds) -> bool:
        x_min, y_min, x_max, y_max = bounds
        return not (
            x_max < self.x_min or x_min > self.x_max
            or y_max < self.y_min or y_min > self.y_max
        )


@dataclass(frozen=True)
class OccupancyAssessment:
    hand_present: Optional[bool]
    valid: bool
    reason: str
    hand_count: int
    hand_bounds: Tuple[Bounds, ...] = ()


def assess_hands(
    hands: object, workspace: Workspace = Workspace()
) -> OccupancyAssessment:
    """Assess lists/tuples of hands, each containing exactly 21 (x, y) pairs.

    An empty successful inference result is clear. Missing, incomplete, malformed
    or out-of-frame landmarks are invalid, never clear. Do not pass failed
    inference as an empty list. Bounding-box overlap intentionally includes cases
    where the workspace lies between landmarks, and touching an edge is occupied.
    """
    if not isinstance(workspace, Workspace):
        raise TypeError("workspace must be a Workspace")
    if not isinstance(hands, (list, tuple)):
        return OccupancyAssessment(None, False, "invalid_landmarks", 0)

    hand_count = len(hands)
    bounds = []
    for hand in hands:
        if not isinstance(hand, (list, tuple)) or len(hand) != 21:
            return OccupancyAssessment(None, False, "invalid_landmarks", hand_count)
        points = []
        for point in hand:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                return OccupancyAssessment(None, False, "invalid_landmarks", hand_count)
            x, y = point
            if not _finite_number(x) or not _finite_number(y):
                return OccupancyAssessment(None, False, "invalid_landmarks", hand_count)
            if not (0 <= x <= 1 and 0 <= y <= 1):
                return OccupancyAssessment(None, False, "landmarks_out_of_frame", hand_count)
            points.append((float(x), float(y)))
        xs, ys = zip(*points)
        bounds.append((min(xs), min(ys), max(xs), max(ys)))

    occupied = any(workspace.intersects(box) for box in bounds)
    return OccupancyAssessment(
        occupied, True, "hand_in_workspace" if occupied else "workspace_clear",
        hand_count, tuple(bounds),
    )
