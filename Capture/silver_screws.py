"""Match unpainted screw heads against a previously registered two-arm station."""

from itertools import combinations

import cv2
import numpy as np
from plate_vision import HOLES, Ambiguous, Hold, project, transform


# Only used to order grids for first-time lens enrollment. Never used to correct the room.
# A forward-looking pinhole in head coordinates. Held-out views qualify the actual lens.
def enrollment_camera(shape):
    h, w = shape[:2]
    k = np.array([[0.65 * w, 0, w / 2], [0, 0.65 * w, h / 2], [0, 0, 1.0]])
    lens = transform(
        np.array([[0.0, 0, 1], [-1, 0, 0], [0, -1, 0]]), [0.055, 0.035, 0.02]
    )
    return k, np.zeros(5), lens


def screw_centers(gray):
    """Fit the outer metal perimeter at two thresholds; ignore the drive recess."""
    levels = []
    bg = cv2.GaussianBlur(gray, (0, 0), 9)
    for delta in (18, 32):
        mask = cv2.compare(cv2.subtract(gray, bg), delta, cv2.CMP_GT)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        hits = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            if (
                min(w, h) < 5
                or max(w, h) > 65
                or len(contour) < 8
                or min(x, y) < 2
                or x + w + 2 >= gray.shape[1]
                or y + h + 2 >= gray.shape[0]
            ):
                continue
            center, axes, _ = cv2.fitEllipse(contour)
            minor, major = sorted(axes)
            if (
                minor < 4
                or minor / major < 0.4
                or not 0.80
                < cv2.contourArea(contour) / (np.pi * minor * major / 4)
                < 1.16
            ):
                continue
            # Check the material against the plate outside the whole perimeter.
            border = 3
            patch = gray[
                max(0, y - border) : y + h + border, max(0, x - border) : x + w + border
            ]
            outer = np.r_[patch[0], patch[-1], patch[:, 0], patch[:, -1]]
            metal = gray[contour[:, 0, 1], contour[:, 0, 0]]
            contrast = np.median(metal) - float(np.median(outer))
            if contrast >= 25:
                hits.append((np.array(center), minor, contrast))
        levels.append(hits)
    stable = []
    for p, minor, contrast in levels[0]:
        matches = [
            q
            for q, diam, _ in levels[1]
            if np.linalg.norm(p - q) < 0.9 and 0.7 < diam / minor < 1.15
        ]
        if len(matches) == 1:
            stable.append((0.5 * (p + matches[0]), minor, contrast))
    return stable


def grids(points):
    """Pair two complete three-screw columns; retain competing orders for the seed check."""
    if len(points) > 48:
        raise Ambiguous("too many screw-like objects in the predicted plate region")
    triples = []
    for i, j in combinations(range(len(points)), 2):
        a, b = points[i], points[j]
        dist = np.linalg.norm(a - b)
        if not 20 < dist < 520:
            continue
        mid = (a + b) * 0.5
        for m in np.flatnonzero(np.linalg.norm(points - mid, axis=1) < 0.13 * dist):
            axis = (b - a) / dist
            offset = points[m] - a
            if abs(axis[0] * offset[1] - axis[1] * offset[0]) <= 2.4:
                triples.append((i, int(m), j))
    if len(triples) > 64:
        raise Ambiguous("too many overlapping screw rows")
    for col1, col2 in combinations(triples, 2):
        if set(col1).intersection(col2):
            continue
        for left, right in ((col1, col2), (col2, col1)):
            for l in (left, left[::-1]):
                for r in (right, right[::-1]):
                    ids = (*l, *r)
                    obs = points[list(ids)]
                    quad = obs[[0, 3, 5, 2]].astype(np.float32)
                    if (
                        not cv2.isContourConvex(quad)
                        or cv2.contourArea(quad, oriented=True) >= -250
                    ):
                        continue
                    mat = cv2.getPerspectiveTransform(
                        HOLES[[0, 3, 5, 2], :2].astype(np.float32), quad
                    )
                    projected = cv2.perspectiveTransform(
                        HOLES[None, :, :2].astype(np.float32), mat
                    )[0]
                    if np.linalg.norm(projected - obs, axis=1).max() <= 2.4:
                        yield ids, obs


def match_screws(image, room, head, layout, optics=None):
    boot = optics is None
    k, d, lens = enrollment_camera(image.shape) if boot else optics
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    features = screw_centers(gray)
    matches = []
    used = set()
    for side, plate in layout.items():
        try:
            pred = project(room, head, lens, plate, k, d)
        except Hold:
            continue
        cam = np.linalg.inv(room @ head @ lens) @ plate
        depth = (cam[:3, :3] @ HOLES.T + cam[:3, 3, None])[2].min()
        # A cold lens needs a broad image search but still a unique six-point pattern.
        pad = (
            min(0.35 * k[0, 0], k[0, 0] * (0.13 / depth + 0.12))
            if boot
            else k[0, 0] * (0.05 / depth + 0.05)
        )
        lo, hi = pred.min(0) - pad, pred.max(0) + pad
        local = [
            i
            for i, (p, _, _) in enumerate(features)
            if np.all(p >= lo) and np.all(p <= hi)
        ]
        points = np.array([features[i][0] for i in local])
        acceptable = []
        for ids, obs in grids(points):
            ref = pred - pred.mean(0)
            measured = obs - obs.mean(0)
            scale = np.sum(ref * measured) / np.sum(ref * ref)
            shape_error = np.max(np.linalg.norm(measured - scale * ref, axis=1))
            span = np.max(np.linalg.norm(ref, axis=1))
            if not (0.60 < scale < 1.55 if boot else 0.85 < scale < 1.20):
                continue
            if (
                shape_error > max(3.0, (0.32 if boot else 0.12) * span)
                or np.linalg.norm(obs.mean(0) - pred.mean(0)) > pad
            ):
                continue
            chosen = tuple(local[i] for i in ids)
            sizes = [features[i][1] for i in chosen]
            if max(sizes) > 2.0 * min(sizes):
                continue
            acceptable.append((chosen, obs))
        if len(acceptable) > 1:
            raise Ambiguous("multiple six-screw matches agree with the prior")
        fully_in_view = np.all(pred.min(0) > 8) and np.all(
            pred.max(0) < np.array(gray.shape[::-1]) - 8
        )
        if not acceptable and not boot and fully_in_view and len(points) >= 3:
            # A visibly incomplete grid invalidates sightings of this plate retained from a previous glance.
            distances = np.linalg.norm(points[:, None, :] - pred[None, :, :], axis=2)
            if (
                len(
                    set(
                        np.argmin(distances, axis=1)[
                            distances.min(axis=1) < min(pad, 20)
                        ]
                    )
                )
                >= 3
            ):
                raise Ambiguous(f"{side} plate partly visible; reacquire both bases")
        if acceptable:
            ids, obs = acceptable[0]
            if used.intersection(ids):
                raise Ambiguous("both bases claim the same screws")
            used.update(ids)
            matches.append(
                (
                    side,
                    obs,
                    min(features[i][2] for i in ids),
                    min(features[i][1] for i in ids),
                )
            )
    if not matches:
        raise Hold(
            "no complete silver screw pattern agrees with the probe or dock pose"
        )
    return matches
