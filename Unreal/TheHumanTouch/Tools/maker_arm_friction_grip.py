"""Squeeze-activated friction grip for kinematic finger pads (simulation runtime only).

The fleet's finger pads are kinematic boxes repositioned every tick; Chaos transfers
little friction from them, so a real side grasp (rubber pads squeezing a paper cup)
rarely carries the cup. This models that friction: while BOTH pads press the same cup
and the jaw is commanded tighter than contact (a squeeze), each tick the cup's velocity
is driven toward the pose it had relative to the gripper when the grip formed. Gravity
stays on, speed is capped (fast moves make the cup lag), and it releases when the jaws
open, the cup leaves the pads, or the cup slips more than SLIP_CM. No actor attachment
or teleport. Results must be labelled "friction-grip model".
"""
import math
import unreal

SLIP_CM = 2.5
TAU_S = .05          # time constant of the friction drive
MAX_SPEED_CM_S = 60.
MAX_SPIN_DEG_S = 360.
GRAVITY_CM_S2 = 980.
MIN_GRIP_GAP_M = .055   # cup body is ~6-8 cm wide; a narrower stop means rim or mouth contact
MAX_GRIP_TILT_DEG = 20.


def _transform(p, q):
    return unreal.Transform(location=unreal.Vector(*p), rotation=unreal.Quat(*q).rotator(), scale=unreal.Vector(1, 1, 1))


def _rotation_error(target, here):
    """Axis (unit) and angle (rad, -pi..pi) rotating `here` onto `target` (world frame)."""
    tx, ty, tz, tw = target.x, target.y, target.z, target.w
    hx, hy, hz, hw = -here.x, -here.y, -here.z, here.w  # conjugate of a unit quaternion
    w = tw*hw-tx*hx-ty*hy-tz*hz
    x = tw*hx+tx*hw+ty*hz-tz*hy
    y = tw*hy-tx*hz+ty*hw+tz*hx
    z = tw*hz+tx*hy-ty*hx+tz*hw
    if w < 0:
        w, x, y, z = -w, -x, -y, -z  # shortest path
    s = math.sqrt(max(0., 1-w*w))
    if s < 1e-6:
        return (0., 0., 1.), 0.
    return (x/s, y/s, z/s), 2*math.acos(min(1., w))


class FrictionGrip:
    def __init__(self):
        self.label = None
        self.relative = None
        self.events = []
        self.last_t = None
        self.last_target = None

    @property
    def active(self):
        return self.label is not None

    def update(self, props, link_pose, contacts, requested, gap, now):
        """Form or release the grip from the jaw command. link_pose: flange (UE loc, quat)."""
        if self.active:
            if self.label not in props or requested > gap+.003:
                return self.release('jaws opened' if self.label in props else 'cup missing', now)
            return None
        if requested < gap-.002 and contacts and contacts[0] in props and gap >= MIN_GRIP_GAP_M:
            # Only a real side grip on the cup body: jaws stopped wide (not on the rim or inside
            # the mouth) and the cup upright when it is taken.
            if props[contacts[0]].get_actor_up_vector().z < math.cos(math.radians(MAX_GRIP_TILT_DEG)):
                return None
            pose = props[contacts[0]].get_actor_transform()
            self.relative = unreal.MathLibrary.make_relative_transform(pose, _transform(*link_pose))
            self.label = contacts[0]
            self.events.append({'t': now, 'event': 'grip', 'prop': self.label, 'gap_m': round(gap, 4)})
            return 'gripped'
        return None

    def follow(self, props, link_pose, now):
        """Every tick while gripped: drive the cup toward its held pose (friction model)."""
        if not self.active:
            return None
        actor = props.get(self.label)
        if actor is None:
            return self.release('cup missing', now)
        c = actor.static_mesh_component
        target = unreal.MathLibrary.compose_transforms(self.relative, _transform(*link_pose))
        here = actor.get_actor_transform()
        error = target.translation-here.translation
        distance = error.length()
        if distance > SLIP_CM:
            return self.release('cup slipped from the pads', now)
        # Track the gripper's own motion, close the remaining error, and pre-compensate the
        # gravity the next physics step will add, so a held cup rests in the pads instead of
        # sagging and being pulled back every tick (gravity itself stays enabled).
        dt = min(.05, max(.005, now-self.last_t)) if self.last_t is not None else 1/60.
        carry = (target.translation-self.last_target)*(1./dt) if self.last_target is not None else unreal.Vector(0, 0, 0)
        self.last_t, self.last_target = now, target.translation
        velocity = carry+error*(1./TAU_S)
        speed = velocity.length()
        if speed > MAX_SPEED_CM_S:
            velocity = velocity*(MAX_SPEED_CM_S/speed)
        c.set_physics_linear_velocity(velocity+unreal.Vector(0, 0, GRAVITY_CM_S2*dt), False)
        axis, angle = _rotation_error(target.rotation, here.rotation)
        spin = max(-MAX_SPIN_DEG_S, min(MAX_SPIN_DEG_S, math.degrees(angle)/TAU_S))
        c.set_physics_angular_velocity_in_degrees(unreal.Vector(*(a*spin for a in axis)), False)
        return 'holding'

    def release(self, reason, now):
        self.events.append({'t': now, 'event': 'release', 'prop': self.label, 'reason': reason})
        self.label = None
        self.relative = None
        self.last_t = self.last_target = None
        return 'released: ' + reason
