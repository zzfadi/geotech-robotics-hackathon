import contextlib
import copy
import io
import json
import unittest

from pose_stream import MAX_AGE, SUBJECTS, Samples, is_pose
from test_pose_stream import pose
from verify_pose_identity import compare_capture, verify


class IdentityTest(unittest.TestCase):
    def test_interpolated_transition_keeps_evaluation_and_source_ids_separate(self):
        now = 900_000_000
        exact = pose(SUBJECTS[0], now, seq=1)
        exact["bones"][1][2:5] = [0.6, -0.2, 1.7]
        exact["bones"][1][5:9] = [0, -0.6, 0, 0.8]
        original = {
            "version": 1,
            "subject": exact["subject"],
            "space": exact["space"],
            **exact["capture_ref"],
            "bones": [
                ["hips", -1, 0, 1, 0, 0, 0, 0, 1, 1],
                ["head", 0, 0.2, 1.7, -0.6, 0.6, 0, 0, 0.8, 1],
            ],
        }
        blended = copy.deepcopy(exact)
        blended["bones"][1][2] += 0.499
        blended["evaluation"].update(tick="2", station_ns=str(now + 10_000_000))
        original_quaternion = exact["bones"][1][5:9]
        equivalent = copy.deepcopy(exact)
        equivalent["bones"][1][5:9] = [-x for x in original_quaternion]
        self.assertTrue(compare_capture(original, equivalent)["matches"])
        difference = compare_capture(original, blended)
        self.assertFalse(difference["matches"])
        self.assertAlmostEqual(difference["max_position_m"], 0.499)
        for index in (9, 5):
            changed = copy.deepcopy(exact)
            if index == 5:
                changed["bones"][1][5:9] = [0, 0, 0, 1]
            else:
                changed["bones"][1][index] = 0.8
            self.assertFalse(compare_capture(original, changed)["matches"])

        samples = Samples()
        samples.sample_clock(now, now, now, now)
        for frame in (exact, blended):
            self.assertTrue(is_pose(frame))
            samples.store(frame)
        self.assertEqual(
            samples.select(now + 15_000_000, 10_000_000, "table_v1")["poses"][
                SUBJECTS[0]
            ],
            exact,
        )
        self.assertEqual(
            samples.select(now + 25_000_000, 10_000_000, "table_v1")["poses"][
                SUBJECTS[0]
            ],
            blended,
        )
        # A new evaluation of the held source must not resurrect disconnected physics inputs.
        held = copy.deepcopy(blended)
        held["evaluation"].update(tick="3", station_ns=str(now + MAX_AGE + 1))
        samples.store(held)
        self.assertEqual(samples.select(now + MAX_AGE + 1, 0, "table_v1")["poses"], {})
        legacy = {
            "type": "hub_pose_v1",
            **{
                k: v
                for k, v in exact.items()
                if k not in ("type", "evaluation", "capture_ref")
            },
            **exact["capture_ref"],
        }
        self.assertFalse(is_pose(legacy))

        missing = copy.deepcopy(exact)
        missing["capture_ref"]["session"] = "separate-run"
        with contextlib.redirect_stdout(io.StringIO()):
            counts = verify(
                [json.dumps(original)],
                [json.dumps(f) for f in (exact, blended, missing)],
            )
        self.assertEqual(counts, {"matched": 1, "different": 1, "missing_witness": 1})
