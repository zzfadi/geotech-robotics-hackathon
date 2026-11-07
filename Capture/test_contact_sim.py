"""Check the two-person contact path, published transforms, and stale input handling."""

import math
import sys
import unittest
from pathlib import Path

import numpy as np
from contact_sim import SIDES, SUBJECTS, Samples, Station

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1] / "Unreal/TheHumanTouch/Tools")
)
from maker_arm_model import mounted_frames, ue_pose

# Use an asymmetric fixture so handedness, per-arm placement and double mounting
# errors cannot hide behind symmetric coordinates.
BASES = {"left": [22.0, -41.0, 76.0], "right": [18.0, 39.0, 77.0]}
ORIENTATION = ue_pose(mounted_frames([0] * 6, yaw=math.pi)["base_link"])[1]
LAYOUT = {
    "space": "fixture_table",
    "table_height_cm": 75.0,
    "bases": {side: [pos, ORIENTATION] for side, pos in BASES.items()},
}


def reach(who, i, stamp, elbow, hand, space="fixture_table"):
    def xyz(v):
        return [-v[1], v[2], -v[0]]

    names = (
        ("right-arm-lower", "right-hand-wrist")
        if who == SUBJECTS[0]
        else ("right_forearm", "right_hand")
    )
    bones = [
        ["root", -1, 0, 1, 0, 0, 0, 0, 1, 1],
        [names[0], 0, *xyz(elbow), 0, 0, 0, 1, 1],
        [names[1], 1, *xyz(hand), 0, 0, 0, 1, 1],
    ]
    return {
        "version": 1,
        "subject": who,
        "session": "fixture_" + who,
        "space": space,
        "seq": str(i),
        "station_ns": str(stamp),
        "bones": bones,
    }


class ContactTest(unittest.TestCase):
    def test_published_rest_links_match_existing_unreal_kinematics(self):
        station = Station(LAYOUT)
        state = station.step({}, 1_000_000_000)
        expected = mounted_frames([0] * 6, 0.04, yaw=math.pi)
        for side in SIDES:
            for link, t in expected.items():
                position, orientation = ue_pose(t)
                actual = state["arms"][side + "_" + link]
                np.testing.assert_allclose(
                    actual[:3], np.array(position) + BASES[side], atol=0.002
                )
                self.assertAlmostEqual(
                    abs(np.dot(actual[3:], orientation)), 1.0, places=5
                )
        self.assertEqual(station.model.neq, 2)

    def test_distinct_reaches_deflect_both_arms_and_expire(self):
        station = Station(LAYOUT)
        samples = Samples(LAYOUT["space"])
        tips = {
            side: station.data.xpos[station.links[side + "_grasp_center"]].copy()
            for side in SIDES
        }
        hit = set()
        maximum = {side: 0.0 for side in SIDES}
        for i in range(100):
            stamp = 1_000_000_000 + i * 16_666_667
            for who, side in zip(SUBJECTS, SIDES):
                hand = tips[side] + [0, 0.20 - min(i * 0.003, 0.35), 0]
                frame = reach(who, i, stamp, hand + [0, 0, 0.22], hand)
                self.assertTrue(samples.receive(frame, stamp))
                self.assertFalse(samples.receive(frame, stamp))
            state = station.step(samples.at(stamp), stamp)
            for who, _, link, _ in state["contacts"]:
                hit.add((who, link.split("_")[0]))
                self.assertEqual(state["inputs"][who]["seq"], str(i))
            for side in SIDES:
                distance = np.linalg.norm(
                    station.data.xpos[station.links[side + "_grasp_center"]]
                    - tips[side]
                )
                maximum[side] = max(maximum[side], distance)
        self.assertEqual(hit, set(zip(SUBJECTS, SIDES)))
        self.assertTrue(all(d > 0.02 for d in maximum.values()))
        now = stamp + 200_000_000
        state = station.step(samples.at(now), now)
        self.assertFalse(state["inputs"] or state["colliders"] or state["contacts"])
        self.assertFalse(
            samples.receive(
                reach(
                    SUBJECTS[0], 101, now, [0, 1, 0], [0, 1.2, 0], space="hip_relative"
                ),
                now,
            )
        )


if __name__ == "__main__":
    unittest.main()
