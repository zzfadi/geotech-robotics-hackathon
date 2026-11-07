"""Exercise actual board detection, camera inversion and headset session registration."""

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
from quest_bridge import SessionRegistration
from register_table import camera, residuals, rigid_fit, save, ue_to_table


class TableRegistrationTest(unittest.TestCase):
    def test_rendered_board_calibrates_camera_and_rejects_camera_move(self):
        # Render a known pinhole camera and compare the recovered physical coordinates.
        k = np.array([[830.0, 0, 480.0], [0, 828.0, 270.0], [0, 0, 1.0]])
        b = cv2.aruco.CharucoBoard(
            (7, 5),
            0.025,
            0.018,
            cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),
        )
        image = b.generateImage((1400, 1000))
        corners = np.float32(
            [[0, 0, 0], [0.175, 0, 0], [0, 0.125, 0], [0.175, 0.125, 0]]
        )
        src = np.float32([[0, 0], [1399, 0], [0, 999], [1399, 999]])
        rng = np.random.default_rng(17)
        table_points = np.array(
            [[0.27, 0.76, -0.05], [0.445, 0.76, -0.05], [0.27, 0.76, 0.075]]
        )
        raw_ue = (
            np.column_stack(
                (-table_points[:, 2], table_points[:, 0], table_points[:, 1])
            )
            * 100
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def render(name, r, t):
                uv = (
                    cv2.projectPoints(corners, r, t, k, None)[0]
                    .reshape(4, 2)
                    .astype(np.float32)
                )
                frame = cv2.warpPerspective(
                    image,
                    cv2.getPerspectiveTransform(src, uv),
                    (960, 540),
                    borderValue=185,
                )
                path = root / name
                cv2.imwrite(str(path), frame)
                return path

            views = []
            for i in range(14):
                r = rng.uniform([-0.5, -0.5, -0.4], [0.5, 0.5, 0.4])
                t = rng.uniform([-0.16, -0.12, 0.42], [0.03, 0.015, 0.7])
                views.append(render(f"lens{i}.png", r, t))
            r = np.array([0.21, -0.18, 0.06])
            t = np.array([-0.09, -0.07, 0.52])
            station = render("fixed.png", r, t)
            check = render("check.png", r, t + np.array([0.00015, 0.0001, 0]))
            opts = SimpleNamespace(
                lens_views=views,
                station_view=station,
                check_view=check,
                max_reprojection_px=3.0,
                max_body_error_px=15.0,
            )
            survey = {"space": "test_table", "board_corners_ue_cm": raw_ue.tolist()}
            result = camera(opts, survey)
            self.assertLess(result["board_check_mean_px"], 1.0)
            camera_pts = corners[:3] @ cv2.Rodrigues(r)[0].T + t
            err = residuals(
                np.array(result["table_from_cv_camera"]), camera_pts, table_points
            )
            self.assertLess(max(err), 0.015)
            opts.check_view = render("moved.png", r, t + np.array([0.025, 0.0, 0.0]))
            with self.assertRaisesRegex(ValueError, "reprojection"):
                camera(opts, survey)

    def test_fingertip_fit_handedness_and_session_lifetime(self):
        src = np.array(
            [
                [0.1, 0.9, -0.2],
                [0.75, 0.9, -0.2],
                [0.22, 1.0, -0.65],
                [0.6, 1.15, -0.42],
            ]
        )
        rotation = cv2.Rodrigues(np.array([0.3, -0.45, 0.25]))[0]
        tgt = src @ rotation.T + [0.4, -0.1, -0.3]
        ue = np.column_stack((-tgt[:, 2], tgt[:, 0], tgt[:, 1])) * 100
        m = rigid_fit(src[:3], ue_to_table(ue[:3]))
        self.assertLess(residuals(m, src[3:], tgt[3:])[0], 1e-8)
        with self.assertRaisesRegex(ValueError, "collinear"):
            rigid_fit(
                [[0, 0, 0], [1, 0, 0], [2, 0, 0]], [[0, 1, 0], [1, 1, 0], [2, 1, 0]]
            )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "quest.json"
            save(
                path,
                {
                    "space": "station",
                    "source_session": "first",
                    "table_from_local_floor": m.tolist(),
                },
            )
            self.assertEqual(
                SessionRegistration(path, "first", None).current()[0], "station"
            )
            self.assertIsNone(SessionRegistration(path, "recentered", None).current())
            self.assertEqual(json.loads(path.read_text())["source_session"], "first")


if __name__ == "__main__":
    unittest.main()
