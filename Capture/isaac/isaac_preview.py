"""Draw registered Hub subjects on the existing Isaac scene's pre-physics callback."""

from pose_stream import SUBJECTS, PoseReceiver
from pxr import Gf, Sdf, Usd, UsdGeom, Vt


class Skeletons:
    """Keep preview geometry in an anonymous sublayer of the current USD session."""

    def __init__(self, stage, parent="/World"):
        self.stage = stage
        self.parent = Sdf.Path(parent)
        if not self.parent.IsAbsolutePath() or not stage.GetPrimAtPath(self.parent):
            raise ValueError("Choose an existing, calibrated frame prim for parent")
        if UsdGeom.GetStageUpAxis(stage) != UsdGeom.Tokens.z:
            raise ValueError("This adapter expects a Z-up stage")
        self.metres_per_unit = UsdGeom.GetStageMetersPerUnit(stage)
        if self.metres_per_unit <= 0:
            raise ValueError("Invalid stage metres per unit")
        root = self.parent.AppendChild("HubPerformers")
        if stage.GetPrimAtPath(root):
            raise ValueError(f"{root} already exists")
        self.layer = Sdf.Layer.CreateAnonymous("hub-performers.usda")
        self.session = stage.GetSessionLayer()
        self.session.subLayerPaths.insert(0, self.layer.identifier)
        self.curves = {}
        self.last = {}
        try:
            with Usd.EditContext(stage, self.layer):
                UsdGeom.Scope.Define(stage, root)
                for name, color in zip(
                    SUBJECTS, ((1, 0.4, 0.08), (0.12, 0.82, 1), (0.7, 0.4, 0.95))
                ):
                    curves = UsdGeom.BasisCurves.Define(stage, root.AppendChild(name))
                    curves.CreateTypeAttr(UsdGeom.Tokens.linear)
                    curves.CreateWrapAttr(UsdGeom.Tokens.nonperiodic)
                    curves.CreateDisplayColorAttr([Gf.Vec3f(*color)])
                    curves.CreateWidthsAttr([0.015 / self.metres_per_unit])
                    curves.SetWidthsInterpolation(UsdGeom.Tokens.constant)
                    curves.CreateVisibilityAttr(UsdGeom.Tokens.invisible)
                    self.curves[name] = curves
        except Exception:
            self.close()
            raise

    def update(self, poses):
        with Usd.EditContext(self.stage, self.layer):
            for name, curves in self.curves.items():
                frame = poses.get(name)
                if frame is None:
                    if name in self.last:
                        curves.GetVisibilityAttr().Set(UsdGeom.Tokens.invisible)
                        del self.last[name]
                    continue
                key = frame["evaluation"]["session"], frame["evaluation"]["tick"]
                if self.last.get(name) == key:
                    continue
                points = []
                bones = frame["bones"]
                for row in bones:
                    index = row[1]
                    if index >= 0 and min(row[-1], bones[index][-1]) >= 0.35:
                        for joint in (bones[index], row):
                            points.append(Gf.Vec3f(*joint[2:5]) / self.metres_per_unit)
                curves.GetCurveVertexCountsAttr().Set(
                    Vt.IntArray([2] * (len(points) // 2))
                )
                curves.GetPointsAttr().Set(Vt.Vec3fArray(points))
                curves.GetVisibilityAttr().Set(
                    UsdGeom.Tokens.inherited if points else UsdGeom.Tokens.invisible
                )
                self.last[name] = key

    def close(self):
        if self.layer.identifier in self.session.subLayerPaths:
            self.session.subLayerPaths.remove(self.layer.identifier)


class IsaacPreview:
    """The existing simulation owner creates this after its physics scene initializes."""

    CALLBACK = "human_touch_hub_pre_step"

    def __init__(
        self,
        simulation_context,
        stage,
        station,
        space,
        parent="/World",
        port=17867,
        delay_ms=100,
        on_applied=None,
    ):
        if not space or space in ("hip_relative", "quest_local_floor"):
            raise ValueError("Supply the measured table space ID")
        self.on_applied = on_applied
        self.context = simulation_context
        self.space = space
        self.receiver = PoseReceiver(station, port, delay_ms)
        self.tick = 0
        self.applied = {}
        self.skeletons = None
        try:
            self.skeletons = Skeletons(stage, parent)
            self.context.add_physics_callback(self.CALLBACK, self.before_step)
        except Exception:
            if self.skeletons:
                self.skeletons.close()
            self.receiver.close()
            raise

    def before_step(self, _dt):
        self.tick += 1
        sample = self.receiver.before_step(self.space)
        self.skeletons.update(sample["poses"])
        self.applied = {
            "tick": self.tick,
            "target_ns": sample["target_ns"],
            "rtt_ms": sample["rtt_ms"],
            "subjects": {
                name: {key: f[key] for key in ("evaluation", "capture_ref")}
                for name, f in sample["poses"].items()
            },
        }

        if self.on_applied and sample["poses"]:
            self.on_applied(self.tick, sample, self.skeletons)

    def close(self):
        self.context.remove_physics_callback(self.CALLBACK)
        self.skeletons.close()
        self.receiver.close()
