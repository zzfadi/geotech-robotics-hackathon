"""Markerless geometry with removable chromatic bore rims. CV coordinates are right-handed metres."""

import math
from dataclasses import dataclass
from itertools import permutations

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

# Rows go across the tabletop from the operator, columns to the operator's right.
# In the right-handed plate frame +Y points to the operator's left.
HOLES = np.array(
    [(x, y, 0.0) for y in (0.0, -0.18) for x in (0.0, 0.04, 0.08)], np.float64
)
FLIP = np.diag([1.0, -1.0, 1.0])
COLORS = {"left": (78, 104), "right": (18, 37), "key": (138, 174)}
FIT = [0, 1, 2, 3, 5]  # middle of the second column is an independent image check


class Hold(ValueError):
    """Observation is incomplete, ambiguous, stale or outside the correction envelope."""


class Ambiguous(Hold):
    """Visible features admit conflicting plate identities or correspondences."""


def transform(r, t):
    a = np.eye(4)
    a[:3, :3], a[:3, 3] = r, t
    return a


def xyz(t, p):
    return np.asarray(p) @ t[:3, :3].T + t[:3, 3]


def ue_pose(position_cm, rotation):
    return transform(FLIP @ rotation @ FLIP, FLIP @ np.asarray(position_cm) / 100)


def room_from_state(s):
    return ue_pose(
        [s["x"], s["y"], s["z"]],
        Rotation.from_euler("z", s["yaw"], degrees=True).as_matrix(),
    )


def room_fields(t):
    p = 100 * FLIP @ t[:3, 3]
    return {
        "x": p[0],
        "y": p[1],
        "z": p[2],
        "yaw": -math.degrees(math.atan2(t[1, 0], t[0, 0])),
    }


def plates(marks):
    p = np.asarray(marks, float) @ FLIP / 100
    if p.shape != (6, 3) or not np.isfinite(p).all():
        raise Hold("invalid surveyed model")
    result = {}
    for side, (a, b, c) in zip(("left", "right"), p.reshape(2, 3, 3)):
        x = (c - a) / 0.08
        y = (a - b) / 0.18
        r = np.column_stack([x, y, np.cross(x, y)])
        if not np.allclose(r.T @ r, np.eye(3), atol=0.007) or r[2, 2] < 0.99:
            raise Hold("CAD flange dimensions/axes differ from survey")
        result[side] = transform(r, a)
    return result


def rings(hsv, gray, hue):
    mask = cv2.inRange(hsv, (hue[0], 95, 62), (hue[1], 255, 255))
    # Chroma is sampled at half resolution on the Quest. Use it to find/color
    # the annulus, then locate the actual bore at full luma resolution.
    outlines, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    lumamask = np.zeros_like(mask)
    for outline in outlines:
        x, y, w, h = cv2.boundingRect(outline)
        if min(w, h) < 7 or max(w, h) > 145:
            continue
        x0, y0 = max(0, x - 3), max(0, y - 3)
        x1, y1 = min(mask.shape[1], x + w + 3), min(mask.shape[0], y + h + 3)
        colors = mask[y0:y1, x0:x1]
        patch = gray[y0:y1, x0:x1]
        values = patch[colors > 0]
        if len(values) < 16:
            continue
        bright = float(np.percentile(values, 65))
        threshold = bright - max(18, 0.33 * bright)
        near = cv2.dilate(colors, np.ones((5, 5), np.uint8))
        lumamask[y0:y1, x0:x1] |= np.where(
            (patch > threshold) & (near > 0), 255, 0
        ).astype(np.uint8)
    contours, tree = cv2.findContours(lumamask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    accepted = []
    if tree is None:
        return accepted
    for i, outer in enumerate(contours):
        child = tree[0, i, 2]
        if (
            tree[0, i, 3] >= 0
            or child < 0
            or len(outer) < 12
            or len(contours[child]) < 5
        ):
            continue
        inner = contours[child]
        co, ao, _ = cv2.fitEllipse(outer)
        ci, ai, _ = cv2.fitEllipse(inner)
        if not (
            8 <= min(ao)
            and max(ao) <= 140
            and min(ao) / max(ao) >= 0.38
            and min(ai) >= 4
            and 0.22 <= min(ai) / min(ao) <= 0.66
            and math.dist(ci, co) <= max(1.5, 0.09 * min(ao))
        ):
            continue
        area = cv2.contourArea(outer)
        if not 0.78 < area / (math.pi * ao[0] * ao[1] / 4) < 1.18:
            continue
        x, y, w, h = cv2.boundingRect(outer)
        if x < 2 or y < 2 or x + w >= mask.shape[1] - 1 or y + h >= mask.shape[0] - 1:
            continue
        inside = np.zeros((h, w), np.uint8)
        cv2.drawContours(inside, [inner - [x, y]], 0, 255, -1)
        inside = cv2.erode(inside, np.ones((3, 3), np.uint8))
        patch = gray[y : y + h, x : x + w]
        bore = patch[inside > 0]
        ring = patch[mask[y : y + h, x : x + w] > 0]
        if len(bore) < 3 or len(ring) < 12:
            continue
        contrast = float(np.median(ring)) - float(np.median(bore))
        if contrast >= 25:
            accepted.append((ci, contrast, min(ai)))
    return accepted


def order_points(key, others):
    """Four corners determine H; both column midpoints must also agree uniquely."""
    points = np.array([key, *others], np.float32)
    src = HOLES[[0, 3, 5, 2], :2].astype(np.float32)
    valid = []
    for perm in permutations(range(1, 6)):
        obs = points[[0, *perm]]
        quad = obs[[0, 3, 5, 2]]
        if (
            not cv2.isContourConvex(quad)
            or cv2.contourArea(quad, oriented=True) >= -250
        ):
            continue
        H = cv2.getPerspectiveTransform(src, quad)
        predicted = cv2.perspectiveTransform(HOLES[None, :, :2].astype(np.float32), H)[
            0
        ]
        if np.max(np.linalg.norm(predicted - obs, axis=1)) <= 1.6:
            valid.append(obs.astype(np.float64))
    if len(valid) > 1:
        raise Ambiguous("multiple valid hole orders")
    return valid[0] if valid else None


def detect(image):
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dots = {s: rings(hsv, gray, v) for s, v in COLORS.items()}
    matches = []
    used_keys = set()
    for side in ("left", "right"):
        if len(dots[side]) > 5:
            raise Ambiguous(f"extra {side} hole rims in view")
        if len(dots[side]) != 5:
            continue
        found = False
        for key in dots["key"]:
            ordered = order_points(key[0], [x[0] for x in dots[side]])
            if ordered is None:
                continue
            if found or key[0] in used_keys:
                raise Ambiguous("ambiguous keyed corner or plate identity")
            found = True
            used_keys.add(key[0])
            candidates = [key, *dots[side]]
            matches.append(
                (
                    side,
                    ordered,
                    min(x[1] for x in candidates),
                    min(x[2] for x in candidates),
                )
            )
        if not found:
            raise Ambiguous(f"{side} rim set has no verifiable keyed corner")
    if not matches:
        raise Hold(
            "look closer at one plate; need one keyed grid of six distinct high-contrast dark holes"
        )
    return matches


@dataclass
class Observation:
    side: str
    uv: np.ndarray
    head: np.ndarray  # tracking_from_head at the stationary exposure
    stamp: float  # host wall time at the end of the camera read
    sensor_ns: int = 0


def project(room, head, lens, target, K, D):
    cam_from_plate = np.linalg.inv(room @ head @ lens) @ target
    pos = xyz(cam_from_plate, HOLES)
    if np.min(pos[:, 2]) < 0.08:
        raise Hold("plate behind camera or too close")
    xy, _ = cv2.projectPoints(pos, np.zeros(3), np.zeros(3), K, D)
    return xy.reshape(-1, 2)


def lens_matrix(v):
    return transform(Rotation.from_rotvec(v[:3]).as_matrix(), v[3:])


def calibrate(obs, layout, room, size):
    """Estimate the effective lens from these exact YUV buffers, and its rigid head mount."""
    if len(obs) < 16 or any(sum(o.side == s for o in obs) < 8 for s in layout):
        raise Hold("need at least eight diverse captures of each base")
    held = []
    train = []
    for side in layout:
        group = [o for o in obs if o.side == side]
        held.extend(group[::4])
        train.extend(o for i, o in enumerate(group) if i % 4)
    centers = np.array([o.uv.mean(0) for o in train])
    if np.any(np.ptp(centers, axis=0) < np.array(size) * 0.30):
        raise Hold("cover more of the image horizontally and vertically")
    if max(
        Rotation.from_matrix(a.head[:3, :3].T @ b.head[:3, :3]).magnitude()
        for a in train
        for b in train
    ) < math.radians(24):
        raise Hold("insufficient viewpoint diversity")
    rms, K, D, _, _ = cv2.calibrateCamera(
        [HOLES.astype(np.float32)] * len(train),
        [o.uv.astype(np.float32) for o in train],
        size,
        None,
        None,
        flags=cv2.CALIB_ZERO_TANGENT_DIST | cv2.CALIB_FIX_K3,
    )
    if not (
        np.isfinite(K).all()
        and np.isfinite(D).all()
        and rms < 1.1
        and 0.35 * size[0] < K[0, 0] < 2.0 * size[0]
        and 0.9 < K[0, 0] / K[1, 1] < 1.1
        and abs(K[0, 2] / size[0] - 0.5) < 0.14
        and abs(K[1, 2] / size[1] - 0.5) < 0.14
    ):
        raise Hold("invalid or weak camera calibration")
    seeds = []
    normals = []
    for o in train:
        valid, rs, ts, _ = cv2.solvePnPGeneric(
            HOLES, o.uv, K, D, flags=cv2.SOLVEPNP_IPPE
        )
        if not valid:
            continue
        for r, t in zip(rs, ts):
            cp = transform(cv2.Rodrigues(r)[0], t.ravel())
            if np.linalg.inv(cp)[2, 3] <= 0:
                continue
            lens = np.linalg.inv(room @ o.head) @ layout[o.side] @ np.linalg.inv(cp)
            if np.linalg.norm(lens[:3, 3]) < 0.22 and lens[0, 2] > 0.65:
                seeds.append(
                    np.r_[Rotation.from_matrix(lens[:3, :3]).as_rotvec(), lens[:3, 3]]
                )
                normals.append(cp[:3, 2])
                break
    if len(seeds) < len(train) * 0.75 or np.min(
        np.asarray(normals) @ np.asarray(normals).T
    ) > math.cos(math.radians(18)):
        raise Hold(
            "insufficient oblique views, or the physical setup disagrees with the calibration seed"
        )

    def residual(v, frames):
        lens = lens_matrix(v)
        return np.concatenate(
            [
                (project(room, o.head, lens, layout[o.side], K, D) - o.uv).ravel()
                for o in frames
            ]
        )

    opt = least_squares(
        residual, seeds[0], args=(train,), loss="soft_l1", f_scale=0.8, max_nfev=80
    )
    lens = lens_matrix(opt.x)
    errors = np.linalg.norm(residual(opt.x, held).reshape(-1, 2), axis=1)
    if (
        not opt.success
        or not np.isfinite(errors).all()
        or np.max(errors) > 2
        or np.sqrt(np.mean(errors**2)) > 1.1
        or np.linalg.norm(lens[:3, 3]) > 0.20
    ):
        raise Hold("independent left/right views failed camera-to-head calibration")
    return (
        K,
        D,
        lens,
        {
            "training_rms_px": float(rms),
            "held_max_px": float(np.max(errors)),
            "held_rms_px": float(np.sqrt(np.mean(errors**2))),
        },
    )


def correction(v, room, center):
    R = Rotation.from_euler("z", v[3]).as_matrix()
    return transform(R, center + v[:3] - R @ center) @ room


def estimate(obs, layout, room, K, D, lens, now):
    """Both bases, independent side fits, held-out bore, no scale or tilt updates."""
    if (
        not obs
        or now - min(o.stamp for o in obs) > 12
        or max(o.stamp for o in obs) > now + 0.01
    ):
        raise Hold("stale base sightings")
    if len({(o.side, o.sensor_ns) for o in obs}) != len(obs):
        raise Hold("replayed exposure")
    center = np.mean([a[:3, 3] for a in layout.values()], axis=0)
    grouped = {s: [o for o in obs if o.side == s] for s in layout}

    def solve(frames):
        def fun(v):
            m = correction(v, room, center)
            return np.concatenate(
                [
                    (
                        project(m, o.head, lens, layout[o.side], K, D)[FIT] - o.uv[FIT]
                    ).ravel()
                    for o in frames
                ]
            )

        opt = least_squares(
            fun,
            np.zeros(4),
            bounds=([-0.08, -0.08, -0.08, -0.09], [0.08, 0.08, 0.08, 0.09]),
            loss="soft_l1",
            f_scale=0.75,
        )
        m = correction(opt.x, room, center)
        err = np.concatenate(
            [
                np.linalg.norm(
                    project(m, o.head, lens, layout[o.side], K, D) - o.uv, axis=1
                )
                for o in frames
            ]
        )
        cov = np.linalg.pinv(opt.jac.T @ opt.jac) * 0.5**2
        if (
            not opt.success
            or np.linalg.matrix_rank(opt.jac) < 4
            or np.max(err) > 1.75
            or np.sqrt(np.mean(err**2)) > 1.0
            or max(np.sqrt(np.diag(cov)[:3])) > 0.006
        ):
            raise Hold("weak pose geometry or independent reprojection check failed")
        return m, opt.x

    fits = []
    for frames in grouped.values():
        if (
            len(frames) < 3
            or max(o.stamp for o in frames) - min(o.stamp for o in frames) < 0.4
        ):
            raise Hold("need three fresh sightings of each base")
        if (
            max(
                np.linalg.norm(a.head[:3, 3] - b.head[:3, 3])
                for a in frames
                for b in frames
            )
            < 0.025
        ):
            raise Hold("shift viewpoint slightly at each base")
        fits.append(solve(frames)[0])
    refs = np.array([a[:3, 3] for a in layout.values()])
    anchors = xyz(np.linalg.inv(room), refs)
    disagreement = max(
        np.linalg.norm(xyz(fits[0], anchors) - xyz(fits[1], anchors), axis=1)
    )
    if disagreement > 0.006 or Rotation.from_matrix(
        fits[0][:3, :3].T @ fits[1][:3, :3]
    ).magnitude() > math.radians(0.65):
        raise Hold("independent left and right base corrections conflict")
    m, v = solve(obs)
    movement = max(np.linalg.norm(xyz(m, anchors) - refs, axis=1))
    if movement > 0.04 or abs(v[3]) > math.radians(2.5):
        raise Hold("outside small-drift capture range; use dock or survey")
    if movement < 0.002:
        raise Hold("already aligned within 2 mm; holding")
    return m, {
        "shift_mm": round(1000 * movement, 2),
        "agreement_mm": round(1000 * disagreement, 2),
    }
