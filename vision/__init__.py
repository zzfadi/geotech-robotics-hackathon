"""Handoff moments for the hybrid loop: a fast local detector says *when*, Dream or a script says *how*.

Nothing here opens a camera, loads a model, calls the Dream API or moves an arm
unless a caller explicitly constructs the optional adapters in ``yolo_source``.
The logic modules are standard-library only, like ``turn_gate``.
"""

from .detections import Detection, FrameDetections
from .moments import MomentConfig, MomentEvent, MomentTracker
from .hybrid import ExecutorRouter, HandoffCoordinator, Plan

__all__ = [
    "Detection", "FrameDetections", "MomentConfig", "MomentEvent", "MomentTracker",
    "ExecutorRouter", "HandoffCoordinator", "Plan",
]
