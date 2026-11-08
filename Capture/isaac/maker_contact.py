"""Measure a streamed hand's contact with an articulated Maker Arm, on Luna2."""

import argparse
import json
import math
import time
import traceback
import urllib.request
from pathlib import Path

from bridge_trial import fingerprint
from contact_fixture import CLEARANCE, SUBJECT
from pose_stream import PoseReceiver

HAND = "/World/TrialPerformerHand"
DEFAULT_SCENE = "F:/IsaacSim/scene/TheHumanTouch_isaac.usda"


def simulate(app, args, emit):
    import numpy as np
    import omni.physics.tensors as tensors
    import omni.usd
    from omni.physx import get_physx_simulation_interface
    from isaacsim.core.api import World
    from pxr import Usd, UsdGeom, UsdPhysics, PhysxSchema, PhysicsSchemaTools, Gf

    if not args.scene.is_file():
        raise FileNotFoundError(args.scene)
    ctx = omni.usd.get_context()
    ctx.open_stage(args.scene.as_posix())
    while ctx.get_stage_loading_status()[2] > 0:
        app.update()
    stage = ctx.get_stage()
    stage.SetEditTarget(stage.GetSessionLayer())
    if UsdGeom.GetStageUpAxis(stage) != "Z" or not math.isclose(
        UsdGeom.GetStageMetersPerUnit(stage), 1
    ):
        raise ValueError("Expected the metre, Z-up parity scene")
    links = [
        p
        for p in stage.Traverse()
        if p.HasAPI(UsdPhysics.RigidBodyAPI) and p.GetName().lower() == "link_007"
    ]
    if not links:
        raise ValueError("No Maker Arm link_007 rigid body found")
    link = min(
        links, key=lambda p: ("right" not in str(p.GetPath()).lower(), str(p.GetPath()))
    )
    path = str(link.GetPath())
    colliders = [p for p in Usd.PrimRange(link) if p.HasAPI(UsdPhysics.CollisionAPI)]
    if not colliders:
        raise ValueError(f"No collision geometry below {path}")
    scenes = [str(p.GetPath()) for p in stage.Traverse() if p.IsA(UsdPhysics.Scene)]
    if len(scenes) != 1:
        raise ValueError(f"Expected one physics scene, got {scenes}")
    world = World(
        stage_units_in_meters=1.0,
        physics_dt=1 / 60,
        rendering_dt=1 / 60,
        physics_prim_path=scenes[0],
    )
    sphere = UsdGeom.Sphere.Define(stage, HAND)
    sphere.CreateRadiusAttr(0.035)
    sphere.AddTranslateOp().Set(Gf.Vec3d(0, 0, 50))
    body = sphere.GetPrim()
    UsdPhysics.RigidBodyAPI.Apply(body).CreateKinematicEnabledAttr(True)
    UsdPhysics.CollisionAPI.Apply(body)
    PhysxSchema.PhysxContactReportAPI.Apply(body).CreateThresholdAttr(0.0)
    # Only this pair can exert force from the test hand.
    pairs = UsdPhysics.FilteredPairsAPI.Apply(body).CreateFilteredPairsRel()
    for p in stage.Traverse():
        if (
            p.HasAPI(UsdPhysics.CollisionAPI) or p.HasAPI(UsdPhysics.RigidBodyAPI)
        ) and p != body:
            if not p.GetPath().HasPrefix(
                link.GetPath()
            ) and not link.GetPath().HasPrefix(p.GetPath()):
                pairs.AddTarget(p.GetPath())
    world.reset()
    for _ in range(120):
        world.step(render=False)

    view = tensors.create_simulation_view("numpy")
    wrist = view.create_rigid_body_view(path)
    hand = view.create_rigid_body_view(HAND)
    if wrist.count != 1 or hand.count != 1:
        raise RuntimeError(f"PhysX body counts wrist={wrist.count}, hand={hand.count}")
    transform = wrist.get_transforms()[0].copy()
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(), ["default", "render", "proxy", "guide"]
    )
    bounds = Gf.Range3d()
    for p in colliders:
        bounds.UnionWith(cache.ComputeRelativeBound(p, link).ComputeAlignedRange())
    if bounds.IsEmpty():
        raise ValueError(f"Empty collision bounds for {path}")
    rotation = Gf.Quatd(float(transform[6]), Gf.Vec3d(*map(float, transform[3:6])))
    center = list(
        rotation.Transform(bounds.GetMidpoint()) + Gf.Vec3d(*map(float, transform[:3]))
    )
    meta = dict(
        event="target",
        trial=args.trial,
        link=path,
        center_m=center,
        collider_count=len(colliders),
        hand_radius_m=0.035,
        scene=str(args.scene),
        dt_s=1 / 60,
        playout_ms=100,
    )
    emit(meta)
    if args.target_file:
        pending = args.target_file.with_suffix(".part")
        pending.write_text(json.dumps(meta))
        pending.replace(args.target_file)
    else:
        req = urllib.request.Request(
            args.target_url, data=json.dumps(meta).encode(), method="POST"
        )
        with urllib.request.urlopen(req, timeout=8) as res:
            if res.status != 200:
                raise RuntimeError("Station rejected the target")
    park = [center[0], center[1] - CLEARANCE, center[2]]
    targets = np.array([[0, 0, 50, 0, 0, 0, 1]], dtype=np.float32)
    indices = np.array([0], dtype=np.int32)
    events = []

    def on_contacts(headers, points):
        stamp = time.monotonic_ns()
        for h in headers:
            actors = {
                str(PhysicsSchemaTools.intToSdfPath(a)) for a in (h.actor0, h.actor1)
            }
            if actors != {path, HAND}:
                continue
            for index in range(
                h.contact_data_offset, h.contact_data_offset + h.num_contact_data
            ):
                imp = points[index].impulse
                magnitude = (
                    math.sqrt(imp.x**2 + imp.y**2 + imp.z**2)
                    if hasattr(imp, "x")
                    else float(np.linalg.norm(imp))
                )
                if magnitude > 0:
                    events.append((stamp, magnitude))

    sub = get_physx_simulation_interface().subscribe_contact_report_events(on_contacts)
    receiver = PoseReceiver(args.station, delay_ms=100)
    zero = transform[:3].copy()
    tick = 0
    try:
        end = time.monotonic() + args.seconds
        while time.monotonic() < end and app.is_running():
            before = time.monotonic()
            sample = receiver.before_step("spoof-" + args.trial)
            frame = sample["poses"].get(SUBJECT)
            bone = (
                next(
                    (
                        b
                        for b in frame["bones"]
                        if b[0] == "left_hand" and b[-1] >= 0.35
                    ),
                    None,
                )
                if frame
                else None
            )
            targets[0, :3] = bone[2:5] if bone else park
            hand.set_kinematic_targets(targets, indices)
            events.clear()
            world.step(render=False)
            tick += 1
            if bone:
                row = dict(
                    event="step",
                    tick=tick,
                    subject=SUBJECT,
                    evaluation=frame["evaluation"],
                    capture_ref=frame["capture_ref"],
                    sha256=fingerprint(frame),
                    hand_m=bone[2:5],
                    hand_actual_m=hand.get_transforms()[0, :3].tolist(),
                    wrist_pose=wrist.get_transforms()[0].tolist(),
                    wrist_displacement_m=float(
                        np.linalg.norm(wrist.get_transforms()[0, :3] - zero)
                    ),
                    wrist_speed_m_s=float(
                        np.linalg.norm(wrist.get_velocities()[0, :3])
                    ),
                    impulse_Ns=sum(v for _, v in events),
                    clock_rtt_ms=sample["rtt_ms"],
                )
                if events:
                    row["evaluation_to_response_ms"] = (
                        events[0][0]
                        + sample["clock_offset_ns"]
                        - int(frame["evaluation"]["station_ns"])
                    ) / 1e6
                emit(row)
            time.sleep(max(0, 1 / 60 - (time.monotonic() - before)))
        emit(dict(event="end", ticks=tick))
    finally:
        receiver.close()
        del sub
        world.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", required=True)
    parser.add_argument("--trial", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--scene", type=Path, default=Path(DEFAULT_SCENE))
    registration = parser.add_mutually_exclusive_group(required=True)
    registration.add_argument("--target-url", help="POST target JSON to the station")
    registration.add_argument(
        "--target-file", type=Path, help="Publish target JSON on a shared trial folder"
    )
    parser.add_argument("--seconds", type=float, default=40)
    args = parser.parse_args()
    with args.output.open("x", buffering=1) as out:

        def emit(row):
            out.write(json.dumps(row, allow_nan=False) + "\n")

        app = None
        try:
            from isaacsim import SimulationApp

            app = SimulationApp({"headless": True})
            simulate(app, args, emit)
        except Exception:
            emit({"event": "error", "traceback": traceback.format_exc()})
            traceback.print_exc()
        finally:
            out.flush()
            if app is not None:
                app.close()
