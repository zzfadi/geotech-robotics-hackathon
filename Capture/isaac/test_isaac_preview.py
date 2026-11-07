import unittest

from isaac_preview import Skeletons
from pxr import Usd, UsdGeom
from test_pose_stream import pose


class PreviewTest(unittest.TestCase):
    def test_stage_units_link_geometry_expiry_and_layer_cleanup(self):
        stage = Usd.Stage.CreateInMemory()
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 0.01)
        UsdGeom.Xform.Define(stage, "/World")
        original = stage.GetRootLayer().ExportToString()
        artists = Skeletons(stage)
        frame = pose("Human_Quest_A", 100)
        artists.update({"Human_Quest_A": frame})
        curve = artists.curves["Human_Quest_A"]
        self.assertEqual(list(curve.GetCurveVertexCountsAttr().Get()), [2])
        self.assertAlmostEqual(curve.GetPointsAttr().Get()[1][2], 172, places=4)
        artists.update({})
        self.assertEqual(curve.GetVisibilityAttr().Get(), UsdGeom.Tokens.invisible)
        artists.close()
        self.assertFalse(stage.GetPrimAtPath("/World/HubPerformers"))
        self.assertEqual(original, stage.GetRootLayer().ExportToString())
