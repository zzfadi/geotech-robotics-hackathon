import json
import math
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from contact_fixture import hand_sweep, SUBJECT as CONTACT_SUBJECT
from verify_maker_contact import summarize


class MakerContactTest(unittest.TestCase):
    def test_hand_reaches_remote_target_in_export_coordinate_system(self):
        center = [2.0, 3.0, 1.0]
        for phase, offset in [(20, -0.22), (80, 0.0), (145, -0.22)]:
            bones = hand_sweep(phase, center)
            _, _, x, y, z, *_ = next(row for row in bones if row[0] == "left_hand")
            self.assertLess(math.dist((-z, -x, y), [2, 3 + offset, 1]), 1e-9)

    def test_false_contact_at_rest_or_wrong_pose_invalidates_trial(self):
        with tempfile.TemporaryDirectory() as directory:
            witness, receipt = [Path(directory) / file for file in ("unreal", "isaac")]
            rows = [dict(event="target", trial="trial", center_m=[0, 1, 0])]
            for cycle in range(3):
                for phase in [*range(10, 20), 60, 95]:
                    strike = phase == 60
                    seq = cycle * 150 + phase
                    xyz = [0, 1 if strike else 0.78, 0]
                    rows.append(
                        dict(
                            event="step",
                            subject=CONTACT_SUBJECT,
                            sha256=str(seq),
                            evaluation=dict(tick=str(seq), session="editor"),
                            capture_ref=dict(seq=str(seq), session="trial"),
                            impulse_Ns=float(strike),
                            wrist_pose=[0.01 * strike, 0, 0, 0, 0, 0, 1],
                            evaluation_to_response_ms=110,
                            hand_m=xyz,
                            hand_actual_m=xyz,
                            clock_rtt_ms=30,
                            tick=seq,
                        )
                    )
            rows.append(dict(event="end"))
            witness.write_text("".join(json.dumps(r) + "\n" for r in rows[1:-1]))

            def verify():
                receipt.write_text("".join(json.dumps(r) + "\n" for r in rows))
                return summarize(witness, receipt)["passed"]

            self.assertTrue(verify())
            rows[1]["impulse_Ns"] = 1
            self.assertFalse(verify())
            rows[1]["impulse_Ns"] = 0
            rows[1]["sha256"] = "unmatched"
            self.assertFalse(verify())
