# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["mediapipe==1.2.2", "opencv-contrib-python>=4.10,<5", "numpy>=1.26,<3", "scipy>=1.11,<2"]
# ///
"""Publish a webcam or still-image skeleton to the local Live Link source and robot observer."""

import argparse
import json
import socket
import sys
import time
import uuid
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from mediapipe.tasks.python import vision
from scipy.spatial.transform import Rotation

# MediaPipe world landmarks use +X right, +Y down and positive Z away from the camera.
CAMERA_TO_XR = np.diag([1.0, -1.0, -1.0])
# A midpoint references a pair of MediaPipe points. Order is parent before child.
RIG = [
    ("pelvis", -1, (23, 24)),
    ("chest", 0, (11, 12)),
    ("head", 1, (7, 8)),
    ("left_upper_arm", 1, 11),
    ("left_forearm", 3, 13),
    ("left_hand", 4, 15),
    ("left_index", 5, 19),
    ("left_pinky", 5, 17),
    ("left_thumb", 5, 21),
    ("right_upper_arm", 1, 12),
    ("right_forearm", 9, 14),
    ("right_hand", 10, 16),
    ("right_index", 11, 20),
    ("right_pinky", 11, 18),
    ("right_thumb", 11, 22),
    ("left_thigh", 0, 23),
    ("left_shin", 15, 25),
    ("left_foot", 16, 27),
    ("left_toe", 17, 31),
    ("right_thigh", 0, 24),
    ("right_shin", 19, 26),
    ("right_foot", 20, 28),
    ("right_toe", 21, 32),
]
# Which point sets the long axis of each articulated bone. Leaves inherit orientation.
AIM = {
    0: 1,
    1: 2,
    3: 4,
    4: 5,
    5: 6,
    9: 10,
    10: 11,
    11: 12,
    15: 16,
    16: 17,
    17: 18,
    19: 20,
    20: 21,
    21: 22,
}


def orientation(direction, lateral, fallback):
    length = np.linalg.norm(direction)
    if length < 1e-5:
        return fallback
    y = direction / length
    x = lateral - y * np.dot(lateral, y)
    if np.linalg.norm(x) < 1e-4:
        # Use the previous twist axis near a shoulder-axis singularity.
        x = Rotation.from_quat(fallback).as_matrix()[:, 0]
        x -= y * np.dot(x, y)
        if np.linalg.norm(x) < 1e-5:
            return fallback
    x /= np.linalg.norm(x)
    return Rotation.from_matrix(np.column_stack((x, y, np.cross(x, y)))).as_quat()


def station_points(world, image, confidence, calibration, image_size):
    if calibration is None:
        return world @ CAMERA_TO_XR, "hip_relative"
    if list(image_size) != calibration["image_size"]:
        raise ValueError("Camera resolution differs from calibration")
    intrinsics = np.asarray(calibration["K"], dtype=float).reshape(3, 3)
    distortion = np.asarray(calibration["distortion"], dtype=float)
    table_from_cv = np.asarray(
        calibration["table_from_cv_camera"], dtype=float
    ).reshape(4, 4)
    # Fit the translation of the hip-centred skeleton in the camera frame using
    # undistorted rays. The model has already estimated the person's orientation.
    good = confidence > 0.65
    if good.sum() < 6:
        return None, None
    pixels = (image[good] * image_size).reshape(-1, 1, 2)
    rays = cv2.undistortPoints(pixels, intrinsics, distortion).reshape(-1, 2)
    local = world[good]
    matrix = np.tile(np.eye(2, 3), (len(rays), 1))
    matrix[:, 2] = -rays.ravel()
    rhs = (rays * local[:, 2:3] - local[:, :2]).ravel()
    shift, _, rank, _ = np.linalg.lstsq(matrix, rhs, rcond=None)
    camera_pts = world + shift
    if rank < 3 or (camera_pts[good, 2] <= 0.05).any():
        return None, None
    reprojected = camera_pts[good, :2] / camera_pts[good, 2:3]
    if (
        np.median(
            np.linalg.norm((reprojected - rays) * np.diag(intrinsics)[:2], axis=1)
        )
        > calibration["max_pixel_error"]
    ):
        return None, None
    return camera_pts @ table_from_cv[:3, :3].T + table_from_cv[:3, 3], calibration[
        "space"
    ]


def solve(result, image_size, rect, calibration, previous):
    if len(result.pose_world_landmarks) != 1:
        return None
    landmarks = result.pose_world_landmarks[0]
    image_landmarks = result.pose_landmarks[0]
    world = np.array([[p.x, p.y, p.z] for p in landmarks])
    xy = np.array([[p.x, p.y] for p in image_landmarks])
    x0, y0, x1, y1 = rect
    xy = (xy * [x1 - x0, y1 - y0] + [x0, y0]) / image_size
    confidence = np.array([min(p.visibility, p.presence) for p in image_landmarks])
    points, space = station_points(world, xy, confidence, calibration, image_size)
    if points is None:
        return None
    xyz = np.array(
        [
            np.mean(points[list(indices)], axis=0)
            if isinstance(indices, tuple)
            else points[indices]
            for _, _, indices in RIG
        ]
    )
    axis = xyz[9] - xyz[3]
    bones, rotations = [], []
    for idx, (name, parent, indices) in enumerate(RIG):
        fallback = previous.get(
            name, rotations[parent] if parent >= 0 else [0.0, 0.0, 0.0, 1.0]
        )
        rotation = (
            orientation(xyz[AIM[idx]] - xyz[idx], axis, fallback)
            if idx in AIM
            else rotations[parent]
        )
        rotations.append(rotation)
        previous[name] = rotation
        score = (
            np.min(confidence[list(indices)])
            if isinstance(indices, tuple)
            else confidence[indices]
        )
        bones.append(
            [
                name,
                parent,
                *np.round(xyz[idx], 6).tolist(),
                *np.round(rotation, 6).tolist(),
                round(float(score), 4),
            ]
        )
    return space, bones


class WebcamPublisher:
    def __init__(self, detector, udp, roi, calibration, observer_port):
        self.detector = detector
        self.udp = udp
        self.roi = roi
        self.calibration = calibration
        self.observer_port = observer_port
        self.begin()

    def begin(self):
        self.session = uuid.uuid4().hex
        self.seq = self.sent = 0
        self.previous = {}
        # Keep MediaPipe VIDEO timestamps monotonic even when browser capture restarts.
        self.last_ms = getattr(self, "last_ms", -1)

    def publish(self, frame, stamp, timing):
        h, w = frame.shape[:2]
        x0, y0, x1, y1 = self.roi
        rect = (int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h))
        crop = frame[rect[1] : rect[3], rect[0] : rect[2]]
        if not crop.size:
            raise ValueError("Camera ROI is empty at this resolution")
        rgb = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        self.last_ms = max(self.last_ms + 1, stamp // 1_000_000)
        result = self.detector.detect_for_video(image, self.last_ms)
        pose = solve(result, (w, h), rect, self.calibration, self.previous)
        seq = self.seq
        self.seq += 1
        if pose is None:
            self.previous.clear()
            return False
        space, bones = pose
        packet = json.dumps(
            {
                "version": 1,
                "subject": "Human_Cam_B",
                "seq": str(seq),
                "station_ns": str(stamp),
                "session": self.session,
                "space": space,
                "bones": bones,
                "timing": timing,
            },
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
        for port in (17860, self.observer_port):
            self.udp.sendto(packet, ("127.0.0.1", port))
        self.sent += 1
        return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model", type=Path, required=True, help="MediaPipe pose_landmarker_lite.task"
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--browser",
        action="store_true",
        help="Serve browser webcam capture on http://localhost:17864",
    )
    source.add_argument("--camera", type=int, help="OpenCV camera index")
    source.add_argument("--image", type=Path, help="Repeat a reference still at 30 Hz")
    parser.add_argument("--frames", type=int, help="Stop after this many input frames")
    parser.add_argument(
        "--roi",
        type=float,
        nargs=4,
        default=(0, 0, 1, 1),
        metavar=("X0", "Y0", "X1", "Y1"),
    )
    parser.add_argument(
        "--calibration",
        type=Path,
        help="Measured camera-to-table and lens calibration JSON",
    )
    parser.add_argument(
        "--observer-port",
        type=int,
        default=17861,
        help="Robot consumer UDP port on localhost",
    )
    args = parser.parse_args()
    x0, y0, x1, y1 = args.roi
    if not (
        0 <= x0 < x1 <= 1
        and 0 <= y0 < y1 <= 1
        and 0 < args.observer_port < 65536
        and args.observer_port != 17860
    ):
        parser.error("Invalid normalized ROI or observer port")
    if args.frames is not None and args.frames < 1:
        parser.error("--frames must be positive")
    calibration = json.loads(args.calibration.read_text()) if args.calibration else None
    if calibration is not None:
        transform = np.array(calibration["table_from_cv_camera"], dtype=float)
        if (
            transform.shape != (4, 4)
            or not np.isfinite(transform).all()
            or not np.allclose(transform[3], [0, 0, 0, 1])
            or not np.allclose(
                transform[:3, :3].T @ transform[:3, :3], np.eye(3), atol=1e-4
            )
            or abs(np.linalg.det(transform[:3, :3]) - 1) > 1e-4
            or not calibration["space"]
            or calibration["max_pixel_error"] <= 0
        ):
            parser.error(
                "Expected a rigid, right-handed table transform, named space and positive pixel error limit"
            )
    still = cv2.imread(str(args.image)) if args.image else None
    if args.image and still is None:
        parser.error("Could not read image")
    cap = cv2.VideoCapture(args.camera) if args.camera is not None else None
    if cap is not None and not cap.isOpened():
        parser.error("Could not open camera. Check camera index and OS camera access.")
    options = vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        running_mode=vision.RunningMode.VIDEO,
        num_poses=2,
    )
    publisher = None
    try:
        with (
            vision.PoseLandmarker.create_from_options(options) as detector,
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp,
        ):
            publisher = WebcamPublisher(
                detector, udp, args.roi, calibration, args.observer_port
            )
            if args.browser:
                from browser_camera import serve

                serve(publisher, args.frames)
                return
            start = time.monotonic()
            while args.frames is None or publisher.seq < args.frames:
                if cap is None:
                    time.sleep(max(0, start + publisher.seq / 30 - time.monotonic()))
                    frame = still
                else:
                    ok, frame = cap.read()
                    if not ok:
                        raise RuntimeError("Camera stopped providing frames")
                publisher.publish(
                    frame,
                    time.monotonic_ns(),
                    "host_dequeue" if cap is not None else "reference_still",
                )
    finally:
        if cap is not None:
            cap.release()
        if publisher:
            print(
                f"Published {publisher.sent}/{publisher.seq} frames, session {publisher.session}",
                file=sys.stderr,
            )
    if not publisher or not publisher.sent:
        raise SystemExit(
            "No single unambiguous pose detected inside the selected region"
        )


if __name__ == "__main__":
    main()
