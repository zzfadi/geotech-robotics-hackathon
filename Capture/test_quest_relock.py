"""Rehearsal regressions with known metric poses. No headset or camera frames needed."""

import json
import math
import socket
import struct
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np
from scipy.spatial.transform import Rotation

sys.path.insert(0, str(Path(__file__).resolve().parent))
from plate_vision import (
    FLIP,
    HOLES,
    Ambiguous,
    Hold,
    Observation,
    calibrate,
    correction,
    estimate,
    project,
    room_fields,
    transform,
    xyz,
)
from quest_relock import capture, track_pose
from silver_screws import match_screws

K = np.array([[780.0, 0, 642], [0, 780.0, 478], [0, 0, 1]])
D = np.array([-0.075, 0.015, 0, 0, 0.0])
LENS = transform(np.array([[0.0, 0, 1], [-1, 0, 0], [0, -1, 0]]), [0.061, 0.034, 0.019])
LAYOUT = {
    s: transform(np.eye(3), np.array([0.1, y, 0.745]))
    for s, y in [("left", 0.45), ("right", -0.35)]
}
ROOM = transform(
    Rotation.from_euler("z", 33, degrees=True).as_matrix(), [0.3, -0.17, -0.52]
)


def view(side, i, room=ROOM):
    plate = LAYOUT[side]
    center = xyz(plate, np.mean(HOLES, 0))
    offset = np.array(
        [
            -0.24 + 0.085 * math.cos(1.75 * i),
            0.13 * math.sin(1.9 * i),
            0.34 + 0.055 * math.cos(0.87 * i),
        ]
    )
    eye = center + offset
    aim = center
    z = aim - eye
    z /= np.linalg.norm(z)
    x = np.cross(z, [0, 0, 1])
    x /= np.linalg.norm(x)
    wc = transform(
        np.column_stack([x, np.cross(z, x), z])
        @ Rotation.from_euler(
            "xy", [18 * math.sin(1.61 * i), 20 * math.cos(2.16 * i)], degrees=True
        ).as_matrix(),
        eye,
    )
    head = np.linalg.inv(room) @ wc @ np.linalg.inv(LENS)
    return Observation(
        side, project(room, head, LENS, plate, K, D), head, i * 0.46, i + 1
    )


def render(side, silver=180, hide=False, index=2):
    o = view(side, index)
    canvas = np.full((960, 1280, 3), 43, np.uint8)
    for idx, p in enumerate(HOLES):
        if hide and idx == 1:
            continue
        for radius, bgr in [
            (0.0038, (silver,) * 3),
            (0.0014, (24,) * 3),
        ]:
            a = np.linspace(0, 2 * math.pi, 64)
            rim = (
                p
                + np.array([radius * np.cos(a), radius * np.sin(a), np.zeros(len(a))]).T
            )
            camera = np.linalg.inv(ROOM @ o.head @ LENS) @ LAYOUT[side]
            px = cv2.projectPoints(xyz(camera, rim), np.zeros(3), np.zeros(3), K, D)[
                0
            ].reshape(-1, 2)
            cv2.fillPoly(
                canvas,
                [np.rint(px * 16).astype(np.int32)],
                bgr,
                lineType=cv2.LINE_AA,
                shift=4,
            )
    return canvas, o


class RelockTest(unittest.TestCase):
    def test_silver_identity_and_holds(self):
        img, o = render("left")

        def match(img, layout=LAYOUT):
            return match_screws(img, ROOM, o.head, layout, (K, D, LENS))

        [(side, uv, contrast, minor)] = match(img)
        self.assertEqual(side, "left")
        self.assertLess(np.linalg.norm(uv - o.uv, axis=1).max(), 1)
        self.assertGreaterEqual(contrast, 25)
        self.assertGreaterEqual(minor, 4)
        with self.assertRaises(Ambiguous):
            match(img, {"left": LAYOUT["left"], "right": LAYOUT["left"]})
        extra = np.roll(img, 22, axis=1)
        with self.assertRaises(Ambiguous):
            match(np.maximum(extra, img))
        for invalid in (render("left", hide=True)[0], render("left", silver=52)[0]):
            with self.assertRaises(Hold):
                match(invalid)

    def test_subsampled_camera_to_correction(self):
        def exposure(side, index, seed=ROOM, optics=None):
            image, o = render(side, index=index)
            yuv = cv2.cvtColor(image, cv2.COLOR_BGR2YUV_I420)
            [(label, o.uv, _, _)] = match_screws(
                cv2.cvtColor(yuv, cv2.COLOR_YUV2BGR_I420), seed, o.head, LAYOUT, optics
            )
            self.assertEqual(label, side)
            return o

        views = [exposure(side, i) for side in LAYOUT for i in range(12)]
        k, d, lens, quality = calibrate(views, LAYOUT, ROOM, (1280, 960))
        self.assertLess(quality["held_max_px"], 1)
        refs = np.array([p[:3, 3] for p in LAYOUT.values()])
        drift = correction(np.array([0.011, -0.008, 0.006, 0.013]), ROOM, refs.mean(0))
        k2, d2, lens2, _ = calibrate(views, LAYOUT, drift, (1280, 960), dock_seed=True)
        self.assertLess(np.linalg.norm(lens[:3, 3] - lens2[:3, 3]), 0.005)
        fresh = [
            exposure(side, 34 + i, drift, (k2, d2, lens2))
            for i, side in enumerate(["left"] * 4 + ["right"] * 4)
        ]
        result, _ = estimate(fresh, LAYOUT, drift, k, d, lens, fresh[-1].stamp + 0.1)
        errors = np.linalg.norm(
            xyz(result, xyz(np.linalg.inv(ROOM), refs)) - refs, axis=1
        )
        self.assertLess(errors.max(), 0.001)
        fresh[-1].sensor_ns = fresh[-2].sensor_ns
        with self.assertRaisesRegex(Hold, "replayed"):
            estimate(fresh, LAYOUT, drift, k, d, lens, fresh[-1].stamp + 0.1)

    def test_lens_and_relock(self):
        training = [view(side, i) for side in LAYOUT for i in range(12)]
        k, d, lens, quality = calibrate(training, LAYOUT, ROOM, (1280, 960))
        self.assertLess(quality["held_max_px"], 0.05)
        rng = np.random.default_rng(8)
        fresh = [view(side, i) for i, side in enumerate(["left"] * 5 + ["right"] * 5)]
        for o in fresh:
            o.uv += rng.normal(0, 0.10, o.uv.shape)
        center = np.mean([m[:3, 3] for m in LAYOUT.values()], 0)
        drift = correction(np.array([0.012, -0.008, 0.006, 0.014]), ROOM, center)
        result, _report = estimate(fresh, LAYOUT, drift, k, d, lens, 5.0)
        here = xyz(np.linalg.inv(ROOM), center)
        self.assertLess(np.linalg.norm(xyz(result, here) - center), 0.001)
        with self.assertRaises(Hold):
            estimate(fresh[:5], LAYOUT, drift, k, d, lens, 5.0)
        with self.assertRaises(Hold):
            estimate(fresh, LAYOUT, drift, k, d, lens, 19.0)
        for o in fresh[5:]:
            o.uv = project(
                correction(np.array([0.024, 0, 0, 0]), ROOM, center),
                o.head,
                LENS,
                LAYOUT[o.side],
                k,
                d,
            )
        with self.assertRaises(Hold):
            estimate(fresh, LAYOUT, drift, k, d, lens, 5.0)

    def test_camera_socket_to_editor_observation(self):
        image, truth = render("left")
        pixels = cv2.cvtColor(image, cv2.COLOR_BGR2YUV_I420).tobytes()
        header = json.dumps(
            {"version": 1, "width": 1280, "height": 960, "timestamp_ns": 4321}
        ).encode()

        def serve(listener):
            peer, _ = listener.accept()
            with peer:
                self.assertEqual(peer.recv(1), b"\x01")
                peer.sendall(struct.pack("!I", len(header)) + header + pixels)

        with tempfile.TemporaryDirectory() as temp_dir, socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(3)
            utc = time.time()
            state = Path(temp_dir) / "state.json"
            position = truth.head[:3, 3] @ FLIP * 100
            quat = Rotation.from_matrix(FLIP @ truth.head[:3, :3] @ FLIP).as_quat()
            marks = [
                list(xyz(plate, HOLES[i]) @ FLIP * 100)
                for plate in LAYOUT.values()
                for i in (0, 3, 2)
            ]
            state.write_text(
                json.dumps(
                    {
                        "utc": utc,
                        "epoch": "test",
                        **room_fields(ROOM),
                        "marks": marks,
                        "revision": 1,
                        "table": 10,
                        "poses": [
                            [utc + i * 0.025, *position, *quat] for i in range(-40, 40)
                        ],
                    }
                )
            )
            with ThreadPoolExecutor(max_workers=1) as worker:
                job = worker.submit(serve, listener)
                measured, meta, [o], _, _ = capture(listener.getsockname()[1], state)
                job.result(timeout=3)
        self.assertEqual(
            (measured["table"], meta["version"], o.side, o.sensor_ns),
            (10, 1, "left", 4321),
        )
        np.testing.assert_allclose(o.head, truth.head, atol=1e-10)
        self.assertLess(np.linalg.norm(truth.uv - o.uv, axis=1).max(), 1)

    def test_still_bracket(self):
        rows = [[i * 0.025, 0, 0, 0, 0, 0, 0, 1] for i in range(25)]
        np.testing.assert_allclose(track_pose(rows, 0.08, 0.48), np.eye(4))
        rows[8][1] = 2
        with self.assertRaises(Hold):
            track_pose(rows, 0.08, 0.48)
        with self.assertRaises(Hold):
            track_pose(rows, -1, 0.48)


if __name__ == "__main__":
    unittest.main()
