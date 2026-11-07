"""Following an agent with a camera: behind and above its ghost, looking where it looks,
kept in front of walls. The Agents tab flies people's viewports with it, and the call
camera (Plugins/CoPresenceCall) films the agent with it for video calls.
"""

import math

import unreal

# The camera sits behind and above the agent, looking where it looks
FOLLOW_DISTANCE = 300.0
FOLLOW_HEIGHT = 100.0
FOLLOW_LOOK_AHEAD = 0.0
FOLLOW_SPEED = 4.0
# The camera turns toward what it looks at a little faster than it moves
TURN_SPEED = 8.0
# How fast the camera swings around the agent to get behind it again after it turns,
# and at most how many degrees a second, so turning around doesn't whip the view
ORBIT_SPEED = 2.0
ORBIT_MAX_DEGREES = 120.0
# Walls, floors and ceilings between the agent and the camera pull the camera in front
# of them, this far from them; it eases back out once the way is clear
WALL_CLEARANCE = 30.0
RELEASE_SPEED = 2.0


def _angle_delta(target: float, current: float) -> float:
    """The shortest turn from current to target, in degrees."""
    return (target - current + 180.0) % 360.0 - 180.0


def _clear_reach(ghost, start: unreal.Vector, end: unreal.Vector) -> float:
    """How far the camera can be from the agent toward end without a wall, floor or
    ceiling coming between them."""
    length = (end - start).length()
    hit = unreal.SystemLibrary.line_trace_single(ghost, start, end, unreal.TraceTypeQuery.TRACE_TYPE_QUERY1, False,
                                                 [], unreal.DrawDebugTrace.NONE, True)
    if hit is None:
        return length
    blocking, inside, _, distance = hit.to_tuple()[:4]
    # An agent inside a wall would pull the camera onto itself; look through that wall
    if not blocking or inside:
        return length
    return max(distance - WALL_CLEARANCE, 0.0)


class FollowRig:
    """One camera's orbit around the agent it follows."""

    def __init__(self) -> None:
        self.orbit = None

    def reset(self) -> None:
        self.orbit = None

    def step(self, ghost, location: unreal.Vector, rotation: unreal.Rotator, delta_seconds: float) -> tuple:
        """Moves the camera from its current pose one step toward behind and above the
        agent, and returns the new pose. The first step starts from wherever the camera is,
        so it flies in toward the agent."""
        if self.orbit is None:
            offset = location - ghost.get_actor_location()
            self.orbit = {"yaw": math.degrees(math.atan2(offset.y, offset.x)),
                          "distance": math.hypot(offset.x, offset.y), "height": offset.z,
                          "reach": offset.length(), "look_offset": unreal.Vector(0.0, 0.0, 0.0)}
        return self._pose(ghost, rotation, delta_seconds)

    def _pose(self, ghost, rotation: unreal.Rotator, delta_seconds: float) -> tuple:
        orbit = self.orbit
        ghost_location = ghost.get_actor_location()
        heading = ghost.get_actor_rotation()
        behind_yaw = heading.yaw + 180.0

        # Swings around the agent rather than cutting through it when the agent turns
        swing = _angle_delta(behind_yaw, orbit["yaw"]) * min(ORBIT_SPEED * delta_seconds, 1.0)
        limit = ORBIT_MAX_DEGREES * delta_seconds
        orbit["yaw"] += min(max(swing, -limit), limit)
        orbit["distance"] = unreal.MathLibrary.f_interp_to(orbit["distance"], FOLLOW_DISTANCE, delta_seconds, FOLLOW_SPEED)
        orbit["height"] = unreal.MathLibrary.f_interp_to(orbit["height"], FOLLOW_HEIGHT, delta_seconds, FOLLOW_SPEED)
        yaw = math.radians(orbit["yaw"])
        boom = unreal.Vector(math.cos(yaw) * orbit["distance"], math.sin(yaw) * orbit["distance"], orbit["height"])
        reach = _clear_reach(ghost, ghost_location, ghost_location + boom)
        # In front of a wall at once, so it never hides the agent, and back out gently
        if reach < orbit["reach"]:
            orbit["reach"] = reach
        else:
            orbit["reach"] = unreal.MathLibrary.f_interp_to(orbit["reach"], reach, delta_seconds, RELEASE_SPEED)
        location = ghost_location + boom * (orbit["reach"] / max(boom.length(), 1.0))

        # From behind it looks past the agent at its work, and at the agent while swinging around
        behind_amount = max(math.cos(math.radians(_angle_delta(behind_yaw, orbit["yaw"]))), 0.0)
        look_offset = heading.get_forward_vector() * (FOLLOW_LOOK_AHEAD * behind_amount)
        orbit["look_offset"] = unreal.MathLibrary.v_interp_to(orbit["look_offset"], look_offset, delta_seconds, FOLLOW_SPEED)
        target_rotation = unreal.MathLibrary.find_look_at_rotation(location, ghost_location + orbit["look_offset"])
        return location, unreal.MathLibrary.r_interp_to(rotation, target_rotation, delta_seconds, TURN_SPEED)
