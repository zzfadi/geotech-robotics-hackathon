"""Offline table-plane measurements from a flat rectangular reference.

Manual image annotations and reference dimensions are inputs, not detections.
No hardware access. This does not calibrate a camera or register an arm base.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def homography(source, destination):
    source = np.asarray(source, dtype=float)
    destination = np.asarray(destination, dtype=float)
    if source.shape != (4, 2) or destination.shape != (4, 2):
        raise ValueError('Four 2D corners required')
    if not np.isfinite(source).all() or not np.isfinite(destination).all():
        raise ValueError('Corners must be finite')
    # Reject crossed, collinear, duplicate or almost point-like quadrilaterals.
    edges = np.roll(source, -1, axis=0) - source
    next_edges = np.roll(edges, -1, axis=0)
    cross = edges[:, 0]*next_edges[:, 1] - edges[:, 1]*next_edges[:, 0]
    if not (np.all(cross > 1e-6) or np.all(cross < -1e-6)):
        raise ValueError('Corners must form a nondegenerate convex perimeter')
    rows, values = [], []
    for (x, y), (u, v) in zip(source, destination):
        rows.extend([[x, y, 1, 0, 0, 0, -u*x, -u*y],
                     [0, 0, 0, x, y, 1, -v*x, -v*y]])
        values.extend([u, v])
    try:
        solution = np.linalg.solve(np.asarray(rows), np.asarray(values))
    except np.linalg.LinAlgError as exc:
        raise ValueError('Reference geometry cannot determine a homography') from exc
    return np.append(solution, 1.).reshape(3, 3)


def project(matrix, points):
    points = np.asarray(points, dtype=float)
    if points.ndim != 2 or points.shape[1] != 2 or not np.isfinite(points).all():
        raise ValueError('Need finite 2D points')
    homogeneous = np.column_stack([points, np.ones(len(points))]) @ matrix.T
    if np.any(np.abs(homogeneous[:, 2]) < 1e-8):
        raise ValueError('Point projects near the plane horizon')
    return homogeneous[:, :2] / homogeneous[:, 2, None]


def measure(observation):
    if observation.get('reference_plane') != 'table' or observation.get('reference_is_flat') is not True:
        raise ValueError('Reference must be confirmed flat on the table')
    size = np.asarray(observation['reference_size_mm'], dtype=float)
    if size.shape != (2,) or not np.isfinite(size).all() or np.any(size <= 0):
        raise ValueError('Need two positive reference dimensions')
    if observation.get('dimension_source') not in ('operator_measured', 'operator_confirmed', 'assumed'):
        raise ValueError('Declare whether dimensions are confirmed or assumed')
    corners = np.asarray(observation['reference_corners_px'], dtype=float)
    points = observation['table_points_px']
    width, height = observation['image_size_px']
    all_points = np.asarray([*corners, *points.values()], dtype=float)
    if (not np.isfinite(all_points).all() or np.any(all_points < 0)
            or np.any(all_points[:, 0] >= width) or np.any(all_points[:, 1] >= height)):
        raise ValueError('Annotation outside image')
    destination = np.array([[0, 0], [size[0], 0], size, [0, size[1]]])
    matrix = homography(corners, destination)
    names = list(points)
    xy = project(matrix, list(points.values()))
    # Sensitivity to the declared corner/point annotation noise. This is not
    # total accuracy: lens distortion, card size and table flatness are separate.
    sigma = float(observation.get('annotation_sigma_px', 1.))
    if not np.isfinite(sigma) or not 0 < sigma <= 5:
        raise ValueError('Annotation sigma must be in (0, 5] pixels')
    rng = np.random.default_rng(0)
    trials = []
    for _ in range(500):
        try:
            candidate = homography(corners + rng.normal(0, sigma, (4, 2)), destination)
            trials.append(project(candidate, np.array(list(points.values())) +
                                  rng.normal(0, sigma, (len(points), 2))))
        except ValueError:
            continue
    if len(trials) < 450:
        raise ValueError('Reference too unstable under annotation uncertainty')
    percentiles = np.percentile(np.asarray(trials), [2.5, 97.5], axis=0)
    return {'schema': 'maker-arm-table-observation-v1', 'motion_ready': False,
            'coordinate_frame': 'card: corner0 origin, corner0->1 +X, corner0->3 +Y; millimetres',
            'reference_size_mm': size.tolist(), 'dimension_source': observation['dimension_source'],
            'pixel_to_table_homography': matrix.tolist(),
            'table_points': {name: {'xy_mm': xy[i].tolist(),
                'annotation_only_95pct_interval_mm': percentiles[:, i, :].tolist()}
                for i, name in enumerate(names)},
            'annotation_sigma_px': sigma, 'robot_base_transforms': None,
            'camera_intrinsics': None, 'object_heights_mm': None,
            'limitations': ['Manual annotations, not automatic object detection',
                'Only points physically on the reference table plane are valid',
                'Intervals exclude lens distortion, uncertain card size and nonflat reference',
                'Four fitted corners are not independent calibration validation',
                'No arm-base registration, jaw calibration, obstacle model or motion permission']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('observation', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Use a new output file')
    observation = json.loads(args.observation.read_text())
    image = Path(observation['image_path'])
    result = measure(observation)
    result.update(image_path=str(image), image_sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                  capture_time=observation.get('capture_time'),
                  annotation_file=str(args.observation))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
