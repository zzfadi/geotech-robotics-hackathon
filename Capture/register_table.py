# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["opencv-contrib-python>=4.10,<5", "numpy>=1.26,<3"]
# ///
"""Register the capture webcam or the current Quest session to surveyed points in Unreal."""

import argparse
import json
import math
import os
import socket
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np

BOARD_SIZE = (7, 5)
SQUARE_M = 0.025
MARKER_M = 0.018


def ue_to_table(points):
    """UE +X forward, +Y right, +Z up in cm to capture +X right, +Y up, -Z forward in m."""
    a = np.asarray(points, dtype=float)
    if a.ndim != 2 or a.shape[1] != 3 or not np.isfinite(a).all():
        raise ValueError("Expected surveyed 3D points in Unreal centimetres")
    return a[:, [1, 2, 0]] * np.array([0.01, 0.01, -0.01])


def rigid_fit(source, target):
    source = np.asarray(source, dtype=float)
    target = np.asarray(target, dtype=float)
    if (
        source.shape != target.shape
        or source.ndim != 2
        or source.shape[1] != 3
        or len(source) < 3
        or not np.isfinite(source).all()
        or not np.isfinite(target).all()
    ):
        raise ValueError("Need at least three finite matching 3D targets")
    x = source - source.mean(axis=0)
    y = target - target.mean(axis=0)
    u, s, vt = np.linalg.svd(x.T @ y)
    if s[1] < 1e-6 or s[1] < s[0] * 0.015:
        raise ValueError(
            "Spread the targets across the table; these points are nearly collinear"
        )
    fix = np.eye(3)
    fix[2, 2] = np.linalg.det(vt.T @ u.T)
    r = vt.T @ fix @ u.T
    t = target.mean(axis=0) - r @ source.mean(axis=0)
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = t
    return m


def residuals(m, source, target):
    return np.linalg.norm(np.asarray(source) @ m[:3, :3].T + m[:3, 3] - target, axis=1)


def save(path, data):
    """Only publish a complete calibration after every independent check has passed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", dir=path.parent, prefix=".registration-", delete=False
    ) as f:
        try:
            json.dump(data, f, indent=2, allow_nan=False)
            f.write("\n")
            f.flush()
            os.replace(f.name, path)
        finally:
            Path(f.name).unlink(missing_ok=True)


def board_observation(path, board, detector):
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        raise ValueError(f"Could not read {path}")
    corners, ids, _, _ = detector.detectBoard(img)
    if ids is None or len(ids) < 8 or board.checkCharucoCornersCollinear(ids):
        raise ValueError(f"Need eight or more widely visible board corners in {path}")
    obj, pixels = board.matchImagePoints(corners, ids)
    return obj.reshape(-1, 3), pixels.reshape(-1, 2), (img.shape[1], img.shape[0])


def camera(args, survey):
    board = cv2.aruco.CharucoBoard(
        BOARD_SIZE,
        SQUARE_M,
        MARKER_M,
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),
    )
    detector = cv2.aruco.CharucoDetector(board)
    all_paths = [*args.lens_views, args.station_view, args.check_view]
    if len({p.resolve() for p in all_paths}) != len(all_paths):
        raise ValueError(
            "Lens views, fixed-board solve and check must be distinct images"
        )
    obs = [board_observation(p, board, detector) for p in all_paths]
    dims = obs[0][2]
    if len(args.lens_views) < 8 or any(o[2] != dims for o in obs):
        raise ValueError(
            "Use at least eight diverse lens views and keep resolution fixed"
        )
    rms, k, dist, rotations, _ = cv2.calibrateCamera(
        [o[0] for o in obs[:-2]], [o[1] for o in obs[:-2]], dims, None, None
    )
    if (
        not np.isfinite(k).all()
        or not np.isfinite(dist).all()
        or not math.isfinite(rms)
        or rms > args.max_reprojection_px
    ):
        raise ValueError(
            f"Lens calibration RMS is {rms:.2f} px; collect more diverse views"
        )
    normals = np.array([cv2.Rodrigues(r)[0][:, 2] for r in rotations])
    if np.min(normals @ normals.T) > math.cos(math.radians(15)):
        raise ValueError(
            "Lens views are all nearly parallel; tilt the board in several directions"
        )
    obj, uv, _ = obs[-2]
    ok, rvec, t = cv2.solvePnP(obj, uv, k, dist)
    if not ok:
        raise ValueError("Fixed-board pose solve failed")
    camera_from_board = np.eye(4)
    camera_from_board[:3, :3] = cv2.Rodrigues(rvec)[0]
    camera_from_board[:3, 3] = t.ravel()
    if np.min((obj @ camera_from_board[:3, :3].T + t.ravel())[:, 2]) <= 0:
        raise ValueError("Board solve is behind the camera")
    check_obj, check_uv, _ = obs[-1]
    pred = cv2.projectPoints(check_obj, rvec, t, k, dist)[0].reshape(-1, 2)
    held = float(np.mean(np.linalg.norm(pred - check_uv, axis=1)))
    if not math.isfinite(held) or held > args.max_reprojection_px:
        raise ValueError(
            f"Independent fixed-board reprojection is {held:.2f} px; check that camera and board stayed fixed"
        )
    xy = np.array(
        [[0, 0, 0], [BOARD_SIZE[0] * SQUARE_M, 0, 0], [0, BOARD_SIZE[1] * SQUARE_M, 0]]
    )
    surveyed = ue_to_table(survey["board_corners_ue_cm"])
    table_from_board = rigid_fit(xy, surveyed)
    fit = float(np.max(residuals(table_from_board, xy, surveyed)))
    if fit > 0.003:
        raise ValueError(
            f"Printed board/UE survey disagree by {fit * 1000:.1f} mm; check scale and corner order"
        )
    return {
        "space": survey["space"],
        "image_size": dims,
        "K": k.tolist(),
        "distortion": dist.ravel().tolist(),
        "table_from_cv_camera": (
            table_from_board @ np.linalg.inv(camera_from_board)
        ).tolist(),
        "max_pixel_error": args.max_body_error_px,
        "lens_rms_px": rms,
        "board_check_mean_px": held,
        "survey_max_mm": 1000 * fit,
    }


def sample_tip(sock, joint, session, seconds=0.8):
    # Ignore packets collected before the operator placed the fingertip.
    start = time.monotonic_ns()
    points = []
    stamps = set()
    while time.monotonic_ns() < start + 2_500_000_000:
        try:
            raw, _ = sock.recvfrom(65535)
        except TimeoutError:
            continue
        frame = json.loads(raw)
        now = time.monotonic_ns()
        if (
            frame.get("subject") != "Human_Quest_A"
            or frame.get("space") != "quest_local_floor"
        ):
            continue
        ns = int(frame["station_ns"])
        if ns < start or ns in stamps or not -50_000_000 <= now - ns <= 120_000_000:
            continue
        if session and frame["session"] != session:
            raise ValueError("Quest session changed; repeat registration")
        side = joint.split("-")[0]
        if not frame.get("optical_index_tips", {}).get(
            side, frame.get("optical_joints", {}).get(side) == 25
        ):
            continue
        row = next((r for r in frame["bones"] if r[0] == joint and r[-1] >= 0.99), None)
        if row is None:
            continue
        session = frame["session"]
        points.append(row[2:5])
        stamps.add(ns)
        if len(points) >= 12 and ns - min(stamps) >= seconds * 1e9:
            break
    if len(points) < 12 or max(stamps) - min(stamps) < seconds * 1e9:
        raise ValueError(
            "No stable optical fingertip interval; keep the hand visible with controllers down"
        )
    p = np.median(points, axis=0)
    if np.quantile(np.linalg.norm(np.asarray(points) - p, axis=1), 0.95) > 0.010:
        raise ValueError("Fingertip moved more than 10 mm; repeat registration")
    return p, session


def quest(args, survey):
    marks = survey["quest_targets"]
    check = survey["quest_check_target"]
    if len(marks) < 3 or len({m["name"] for m in [*marks, check]}) != len(marks) + 1:
        raise ValueError(
            "Supply at least three distinct named fit marks and one separate check mark"
        )
    targets = ue_to_table([m["ue_cm"] for m in marks])
    check_target = ue_to_table([check["ue_cm"]])
    rigid_fit(targets, targets)
    if np.min(np.linalg.norm(targets - check_target[0], axis=1)) < 0.04:
        raise ValueError(
            "Place the independent check at least 4 cm away from any fit mark"
        )
    points = []
    session = None
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", args.port))
        sock.settimeout(0.1)
        for mark in [*marks, check]:
            input(
                f"Hold your {args.hand} index tip on {mark['name']}. Press Return while holding still. "
            )
            p, session = sample_tip(sock, f"{args.hand}-hand-index-tip", session)
            points.append(p)
    transform = rigid_fit(points[:-1], targets)
    fit = float(np.max(residuals(transform, points[:-1], targets)))
    held = float(residuals(transform, points[-1:], check_target)[0])
    if max(fit, held) > 0.020:
        raise ValueError(
            f"Registration missed tolerance. Fit {fit * 1000:.1f} mm; held-out {held * 1000:.1f} mm"
        )
    return {
        "space": survey["space"],
        "source_session": session,
        "table_from_local_floor": transform.tolist(),
        "fit_max_mm": fit * 1000,
        "held_out_mm": held * 1000,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="kind", required=True)
    cam = sub.add_parser("camera")
    cam.add_argument("--lens-views", nargs="+", type=Path, required=True)
    cam.add_argument("--station-view", type=Path, required=True)
    cam.add_argument("--check-view", type=Path, required=True)
    cam.add_argument("--max-reprojection-px", type=float, default=3.0)
    cam.add_argument("--max-body-error-px", type=float, default=15.0)
    head = sub.add_parser("quest")
    head.add_argument("--hand", choices=("left", "right"), default="right")
    head.add_argument("--port", type=int, default=17861)
    for p in (cam, head):
        p.add_argument("--survey", type=Path, required=True)
        p.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    survey = json.loads(args.survey.read_text())
    if not isinstance(survey.get("space"), str) or survey["space"] in (
        "",
        "hip_relative",
        "quest_local_floor",
    ):
        parser.error("Choose a versioned station space")
    if args.kind == "camera" and not all(
        math.isfinite(x) and x > 0
        for x in (args.max_reprojection_px, args.max_body_error_px)
    ):
        parser.error("Pixel tolerances must be positive and finite")
    result = camera(args, survey) if args.kind == "camera" else quest(args, survey)
    save(args.output, result)
    print(
        "Wrote",
        args.output,
        {k: v for k, v in result.items() if k.endswith(("_mm", "_px"))},
    )


if __name__ == "__main__":
    main()
