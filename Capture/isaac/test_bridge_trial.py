import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pxr import Usd, UsdGeom
from bridge_trial import attach, fingerprint
from test_pose_stream import pose


class ApplicationReceiptTest(unittest.TestCase):
    def test_records_only_first_application_after_usd_update(self):
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.Xform.Define(stage, "/World")
        callbacks = {}

        class Context:
            add_physics_callback = staticmethod(callbacks.__setitem__)
            remove_physics_callback = staticmethod(callbacks.__delitem__)

        frame = pose("Human_Cam_B", 1_000_000_000, space="spoof-test")
        sample = dict(
            target_ns=1_000_000_000,
            rtt_ms=12.0,
            clock_offset_ns=100_000_000,
            poses={"Human_Cam_B": frame},
        )
        with tempfile.TemporaryDirectory() as folder, patch(
            "isaac_preview.PoseReceiver"
        ) as receiver:
            receiver.return_value.before_step.return_value = sample
            dest = Path(folder) / "tick.jsonl"
            preview = attach(Context(), stage, "127.0.0.1", "test", dest)
            try:
                with patch(
                    "bridge_trial.time.monotonic_ns", return_value=1_050_000_000
                ):
                    callbacks[preview.CALLBACK](1 / 60)
                    callbacks[preview.CALLBACK](1 / 60)
                (row,) = [json.loads(line) for line in dest.read_text().splitlines()]
                self.assertTrue(row["geometry_ok"])
                self.assertEqual(row["tick"], 1)
                self.assertEqual(row["sha256"], fingerprint(frame))
                self.assertAlmostEqual(row["evaluation_to_apply_ms"], 150.0)
                self.assertAlmostEqual(row["capture_to_apply_ms"], 150.000001)
            finally:
                preview.close()
