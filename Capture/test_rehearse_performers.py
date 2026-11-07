import json
import unittest
from unittest.mock import Mock

from rehearse_performers import POSES, SUBJECTS, publish, skeleton


class RehearsalTest(unittest.TestCase):
    def test_camera_feet_stay_planted_under_moving_pelvis(self):
        samples = {
            pose: {row[0]: row[2:5] for row in skeleton("Human_Cam_B", pose)}
            for pose in ("neutral", "step", "squat")
        }
        for pose in ("step", "squat"):
            for bone in ("left_foot", "right_foot", "left_toe", "right_toe"):
                self.assertEqual(samples["neutral"][bone], samples[pose][bone])
            self.assertNotEqual(samples["neutral"]["pelvis"], samples[pose]["pelvis"])

        lifted = {row[0]: row[2:5] for row in skeleton("Human_Cam_B", "lift")}
        for joint in ("foot", "toe"):
            self.assertEqual(lifted["right_" + joint], samples["neutral"]["right_" + joint])
            self.assertAlmostEqual(
                lifted["left_" + joint][1] - samples["neutral"]["left_" + joint][1], 0.10
            )

    def test_headset_curl_is_isolated_and_all_frames_use_hub_only(self):
        sock = Mock()
        for who in SUBJECTS:
            for sequence, pose in enumerate(POSES):
                publish(sock, who, pose, "fixture", sequence)
                packet, destination = sock.sendto.call_args.args
                self.assertEqual(destination, ("127.0.0.1", 17860))
                frame = json.loads(packet)
                self.assertEqual(frame["timing"], "synthetic_rehearsal")
                rig = frame["bones"]
                self.assertEqual(len(rig), 23 if who == "Human_Cam_B" else 67)
                self.assertEqual(len({row[0] for row in rig}), len(rig))
                for index, row in enumerate(rig):
                    self.assertTrue(row[1] == -1 if index == 0 else 0 <= row[1] < index)
            if who != "Human_Cam_B":
                before = skeleton(who, "reach")
                after = skeleton(who, "curl")
                changed = [a[0] for a, b in zip(before, after) if a != b]
                self.assertIn("left-hand-index-tip", changed)
                self.assertTrue(all(n.startswith("left-hand-index") for n in changed))
