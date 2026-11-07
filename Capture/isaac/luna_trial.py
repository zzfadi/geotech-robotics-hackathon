"""Run actual Isaac/PhysX ticks in a temporary headless stage using Isaac's Python."""

import argparse
import time
from pathlib import Path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", required=True)
    parser.add_argument("--trial", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=45)
    args = parser.parse_args()

    from isaacsim import SimulationApp

    app = SimulationApp({"headless": True})
    try:
        import omni.usd
        from isaacsim.core.api import World
        from pxr import UsdGeom
        from bridge_trial import attach

        world = World(stage_units_in_meters=1.0, physics_dt=1 / 60, rendering_dt=1 / 60)
        stage = omni.usd.get_context().get_stage()
        UsdGeom.Xform.Define(stage, "/World")
        world.reset()
        audit = attach(world, stage, args.station, args.trial, args.output)
        try:
            end = time.monotonic() + args.seconds
            while app.is_running() and time.monotonic() < end:
                world.step(render=True)
            print("Isaac callbacks:", audit.tick, "Receipt:", args.output, flush=True)
            if not audit.tick or not args.output.stat().st_size:
                raise RuntimeError("No applied Hub pose recorded on a physics callback")
        finally:
            audit.close()
            world.stop()
    finally:
        app.close()
