"""Optional Ultralytics adapter: one frame in, ``FrameDetections`` out.

Nothing heavy is imported until a detector is constructed, so the logic and its
tests run without ``ultralytics``, ``torch`` or ``opencv``. On the Orin Nano,
export both models once to TensorRT FP16 engines and pass the ``.engine`` paths:

    yolo export model=yolo26n.pt format=engine quantize=16
    yolo export model=yolo26n-pose.pt format=engine quantize=16

(older Ultralytics releases spell ``quantize=16`` as ``half=True``).
"""

from __future__ import annotations

from typing import Optional, Sequence

from .detections import Detection, FrameDetections, hands_from_pose

# COCO-80 class names that can stand in for a can until we fine-tune one.
DEFAULT_KEEP = ("bottle", "cup", "can", "hand", "person")


def detections_from_result(result, *, keep: Sequence[str] = DEFAULT_KEEP):
    """Convert one Ultralytics ``Results`` (detect or pose) to detections.

    Uses normalized ``boxes.xyxyn``; pose results also yield ``hand`` boxes from
    the wrist keypoints because COCO has no hand class.
    """
    names = result.names
    out = []
    boxes = getattr(result, "boxes", None)
    if boxes is not None and len(boxes):
        for xyxy, cls, conf in zip(boxes.xyxyn.tolist(), boxes.cls.tolist(), boxes.conf.tolist()):
            label = str(names[int(cls)])
            if label in keep:
                box = tuple(min(1.0, max(0.0, float(v))) for v in xyxy)
                out.append(Detection(label, min(1.0, max(0.0, float(conf))), box))
    keypoints = getattr(result, "keypoints", None)
    if keypoints is not None and keypoints.xyn is not None and len(keypoints.xyn):
        xyn = keypoints.xyn.tolist()
        confs = keypoints.conf.tolist() if keypoints.conf is not None else [[1.0] * 17 for _ in xyn]
        people = [[(x, y, c) for (x, y), c in zip(person, conf)] for person, conf in zip(xyn, confs)]
        # Ultralytics marks missing keypoints as (0, 0); hands_from_pose skips low confidence.
        out.extend(hands_from_pose([p for p in people if len(p) == 17]))
    return out


class UltralyticsDetector:
    """A can detector plus an optional pose model for hands, same frame."""

    def __init__(self, detect_model: str, pose_model: Optional[str] = None, *,
                 imgsz: int = 640, conf: float = 0.25, device=None) -> None:
        from ultralytics import YOLO  # optional dependency

        self.detect = YOLO(detect_model, task="detect")
        self.pose = YOLO(pose_model, task="pose") if pose_model else None
        self.kwargs = {"imgsz": imgsz, "conf": conf, "verbose": False}
        if device is not None:
            self.kwargs["device"] = device

    def __call__(self, frame, captured_at: float) -> FrameDetections:
        try:
            found = []
            for model in (self.detect, self.pose):
                if model is not None:
                    found.extend(detections_from_result(model(frame, **self.kwargs)[0]))
            return FrameDetections(captured_at, tuple(found), source="ultralytics")
        except Exception as exc:  # a failed inference is invalid, never empty
            return FrameDetections(captured_at, (), valid=False,
                                   error="{}: {}".format(type(exc).__name__, exc))
