# Hollow cup closing limiter

The previous actuator limiter projected a closed outer frustum into one width, so it could latch fingers already inside the empty cup. The corrected source represents material as128 convex wall sectors and one solid bottom frustum. The nominal outer radii remain3/4cm; inner radii2.8/3.8cm run fromZ−5.7cm toZ6cm. The0.3cm bottom remains solid. There is no filled rim cap.

Each material cell is clipped to the finite pad X/Z footprint. The inward leading face is tested against the swept material interval; finite pad thickness bounds trailing overlap, but closing away from an inner wall is not mistaken for exterior force closure. Both sides must meet material before the actuator latches. This is an approximate closing limiter, not a solver contact report or calibrated force controller. Chaos collisions remain active and own physical wall/bottom response. Outward/internal grasping is not implemented by this change.

28 offline contact, cup, startup and grip-hold tests pass, including empty cavity closing, narrower cavity, yaw/tilt, exterior grasp, solid bottom, finite pad-edge wall contact, opening behavior and legacy boxes. The former test expecting a2cm gap to remain latched inside the empty cavity now correctly expects free inward closure. No physical dynamics guard or collision geometry was changed.

A conservative cell-AABB rejection retains128-sector accuracy. Offline100-call timing measured approximately3.1ms per nearby cup in the closing path (previous unfiltered hollow version3.8ms). All18 arms simultaneously closing on one nearby cup each would add roughly56ms, so this is not a fleet real-time guarantee. Opening commands return immediately, and actor broad phase rejects distant cups.32sectors were not substituted: their inner chord error is approximately0.183mm compared with0.0114mm at128, outside the retained fine-envelope regression tolerance. Runtime profiling remains required with the new physics hulls before broad deployment.

Source-only verification at this commit: no live Unreal module was replaced and no inference was launched.
