"""Table 10 test motions for the real-arm link (pure Python; no Unreal import).

Two web test buttons move both Table 10 arms; in bridge drive mode the real arms follow
through real_arm_bridge.py and its own guards (engage tolerance, speed cap, jump rejection).

- idle:    joint move to IDLE_DEG, the folded rest the real arms sit in when powered off,
           held 2 deg inside the motor limits (the fleet's idle pose is outside the real
           joint-4 range, so it cannot be shared).
- move_up: both grippers straight up MOVE_UP_CM, orientation kept, solved as a chain of
           0.5 cm IK waypoints within the drive limits. Rejected if any waypoint misses.

Speeds stay under MAX_SPEED_RAD_S (the bridge caps the real arm at 0.6 rad/s), with a cosine
ease so the peak joint speed is MAX_SPEED_RAD_S.
"""
import math

import maker_arm_real_mapping as mapping

SIDES = ("left", "right")
IDLE_DEG = (0.0, -6.0, 6.0, 0.0, 0.0, 0.0)
MOVE_UP_CM = 8.0
STEP_CM = 0.5
MAX_SPEED_RAD_S = 0.35
MIN_DURATION_S = 1.5
MAX_IK_ERROR_MM = 3.0
MAX_IK_ERROR_DEG = 3.0
ACTIONS = ("move_up", "idle")


def _ease(s):
    return .5 - .5 * math.cos(math.pi * min(1.0, max(0.0, s)))


def _travel(path):
    return sum(max(abs(b - a) for a, b in zip(p, n)) for p, n in zip(path, path[1:]))


def plan(action, q_now, gaps, yaws, bases, limits):
    """Waypoint paths per side and one shared duration. Raises ValueError when unsafe."""
    if action not in ACTIONS:
        raise ValueError("unknown test action %r" % action)
    paths = {}
    for side in SIDES:
        q0 = list(q_now[side])
        if action == "idle":
            target = mapping.clamp([math.radians(v) for v in IDLE_DEG], limits)
            paths[side] = [q0, target]
            continue
        # The powered-off rest sits just outside the drive limits: step inside them first.
        q = mapping.clamp(q0, limits)
        start_p, start_r = mapping.fk_ue(q, gaps[side], yaws[side], bases[side])["link_007"]
        path = [q0, q] if q != q0 else [q0]
        steps = int(math.ceil(MOVE_UP_CM / STEP_CM))
        for k in range(1, steps + 1):
            target_p = [start_p[0], start_p[1], start_p[2] + MOVE_UP_CM * k / steps]
            q, err_mm, err_deg = mapping.ik(q, gaps[side], yaws[side], bases[side], target_p, start_r, limits)
            if err_mm > MAX_IK_ERROR_MM or err_deg > MAX_IK_ERROR_DEG:
                raise ValueError("%s arm cannot move %.1f cm up from here (%.1f mm, %.1f deg off)"
                                 % (side, MOVE_UP_CM * k / steps, err_mm, err_deg))
            path.append(list(q))
        paths[side] = path
    travel = max(_travel(p) for p in paths.values())
    duration = max(MIN_DURATION_S, math.pi / 2 * travel / MAX_SPEED_RAD_S)
    return {"action": action, "paths": paths, "duration_s": duration,
            "travel_deg": round(math.degrees(travel), 1)}


def sample(motion, t):
    """Joint targets per side at time t (s) along the eased waypoint paths."""
    s = _ease(t / motion["duration_s"])
    out = {}
    for side, path in motion["paths"].items():
        x = s * (len(path) - 1)
        i = min(int(x), len(path) - 2)
        f = x - i
        out[side] = [a + (b - a) * f for a, b in zip(path[i], path[i + 1])]
    return out
