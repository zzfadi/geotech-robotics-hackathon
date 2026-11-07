"""Calibrated webcam landmark confidence must agree with the contact simulator."""

import unittest
from types import SimpleNamespace

import cv2
import numpy as np

from contact_sim import Station
from test_contact_sim import LAYOUT
from webcam import solve


class WebcamRegistrationTest(unittest.TestCase):
    def setUp(self):
        self.size = (960, 540)
        self.intrinsics = np.array(
            [[850.0, 0.0, 480.0], [0.0, 840.0, 270.0], [0.0, 0.0, 1.0]]
        )
        self.distortion = [-0.08, 0.012, 0.001, 0.0, 0.0]
        self.calibration = dict(
            K=self.intrinsics,
            distortion=self.distortion,
            space=LAYOUT["space"],
            table_from_cv_camera=np.eye(4),
            image_size=list(self.size),
            max_pixel_error=8.0,
        )
        self.world = np.zeros((33, 3))
        self.world[:11] = [0.0, -0.64, -0.04]
        self.world[7] += [-0.07, 0.0, 0.03]
        self.world[8] += [0.07, 0.0, 0.03]
        for start, sign in [(11, -1), (12, 1)]:
            for j, (x, y, z) in enumerate(
                [
                    (0.18, -0.45, 0.0),
                    (0.27, -0.24, -0.12),
                    (0.36, -0.07, -0.20),
                    (0.38, -0.04, -0.22),
                    (0.37, -0.02, -0.23),
                    (0.33, -0.03, -0.23),
                    (0.10, 0.0, 0.0),
                    (0.11, 0.43, -0.03),
                    (0.12, 0.84, 0.0),
                    (0.12, 0.87, 0.03),
                    (0.12, 0.88, -0.12),
                ]
            ):
                self.world[start + 2 * j] = [x * sign, y, z]
        self.camera = self.world + [0.17, -0.06, 2.30]
        pixels, _ = cv2.projectPoints(
            self.camera,
            np.zeros(3),
            np.zeros(3),
            self.intrinsics,
            np.array(self.distortion),
        )
        self.uv = pixels.reshape(-1, 2) / self.size

    def solve_frame(self, world, uv):
        observed = [
            SimpleNamespace(x=x, y=y, visibility=0.99, presence=0.99) for x, y in uv
        ]
        predicted = [SimpleNamespace(x=x, y=y, z=z) for x, y, z in world]
        result = SimpleNamespace(
            pose_landmarks=[observed], pose_world_landmarks=[predicted]
        )
        return solve(result, self.size, (0, 0, *self.size), self.calibration, {})

    def test_bad_wrist_withdraws_one_collider_and_preserves_full_body(self):
        _, clean = self.solve_frame(self.world, self.uv)
        wrong = self.world.copy()
        wrong[15, 0] += 0.16
        space, degraded = self.solve_frame(wrong, self.uv)
        by_name = {row[0]: row for row in degraded}
        self.assertEqual(len(by_name), 23)
        self.assertEqual(by_name["left_hand"][9], 0.0)
        self.assertTrue(all(row[9] > 0.9 for row in degraded if row[0] != "left_hand"))
        np.testing.assert_allclose(
            by_name["right_foot"][2:5], self.camera[28], atol=1e-5
        )
        np.testing.assert_allclose(
            by_name["right_hand"][2:5], self.camera[16], atol=1e-5
        )

        station = Station(LAYOUT)
        for seq, bones in enumerate((clean, clean, degraded, clean, clean)):
            stamp = 1_000_000_000 + seq * 16_666_667
            frame = dict(
                seq=str(seq),
                session="synthetic",
                space=space,
                station_ns=str(stamp),
                bones=bones,
            )
            state = station.step({"Human_Cam_B": frame}, stamp)
            active = {side for _, side, _, _ in state["colliders"]}
            self.assertEqual(
                active,
                [set(), {"left", "right"}, {"right"}, {"right"}, {"left", "right"}][
                    seq
                ],
            )

    def test_rejects_frame_when_most_landmarks_disagree(self):
        rng = np.random.default_rng(42)
        offsets = rng.uniform(-0.15, 0.15, self.uv.shape)
        self.assertIsNone(self.solve_frame(self.world, self.uv + offsets))
