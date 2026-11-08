"""Kinematic bench table: a fast stand-in for one Unreal/Isaac cup table.

It is NOT physics. Arm motion is joint-rate limited on the pinned URDF FK; a cup
is held only when the open jaws surround it and close to its diameter, then it
follows the tool rigidly (a friction proxy, never an attachment). Palm, jaw-tip
and table contacts knock cups over or stop the arm. Every sample it reports the
same cup evidence fields the Unreal recorder reports, flagged
physics_simulated=False, so nothing from the bench can pass the Dream export
gate in DreamSim/demonstration_dataset.py.

Layouts come from DreamSim/fleet_planning.cup_layout, so table N and seed S
place the same cups as the real fleet table N with seed S.
"""
import math

import numpy as np

from edge_model.kinematics import Arm, GAP_MAX_M, HOME, ue_to_left_base

FPS = 5
DT = 1.0 / FPS
SUBSTEPS = 4
CUP_HEIGHT = 0.12          # maker_arm_cups.py: 12 cm high
CUP_BASE_R = 0.03          # 6 cm base diameter
CUP_RIM_R = 0.04           # 8 cm rim diameter
JOINT_SPEED = 1.5          # rad/s per joint, conservative for the Maker Arm
GAP_SPEED = 0.15           # m/s jaw travel
PALM_BACK = 0.06           # palm sits 6 cm behind grasp_center along approach
SLIP_SPEED = 0.6           # m/s tool speed that shakes a held cup loose


def cup_radius(height_above_bottom):
    h = min(max(height_above_bottom, 0.0), CUP_HEIGHT)
    return CUP_BASE_R + (CUP_RIM_R - CUP_BASE_R) * h / CUP_HEIGHT


def fleet_layout(table_index, seed):
    """Cups for one table in the left-arm base frame, from the fleet planner."""
    from fleet_planning import cup_layout  # DreamSim, already on sys.path
    cups = []
    for item in cup_layout(table_index, seed):
        p = ue_to_left_base(item['location_cm'])
        cups.append({'label': item['label'], 'xy': [float(p[0]), float(p[1])]})
    return cups


class Cup:
    def __init__(self, label, xy):
        self.label = label
        self.center = np.array([xy[0], xy[1], CUP_HEIGHT / 2])
        self.axis = np.array([0.0, 0.0, 1.0])
        self.state = 'standing'          # standing | held | fallen
        self.rel = None                  # (center, axis) in tool frame while held
        self.speed = 0.0

    def tilt_deg(self):
        return math.degrees(math.acos(max(-1.0, min(1.0, float(self.axis[2])))))

    def lowest_clearance(self):
        tilt = math.radians(self.tilt_deg())
        return float(self.center[2] - (CUP_HEIGHT / 2) * math.cos(tilt) - CUP_RIM_R * math.sin(tilt))


class BenchTable:
    """One seeded table. step() takes absolute joint and grip targets."""

    def __init__(self, table_index, seed, layout=None, q0=None):
        self.arm = Arm()
        self.table_index = table_index
        self.seed = seed
        layout = layout if layout is not None else fleet_layout(table_index, seed)
        self.cups = [Cup(c['label'], c['xy']) for c in layout]
        self.target = self.cups[0].label   # fleet_planning: Cup is always the target
        self.q = self.arm.clip(HOME if q0 is None else q0)
        self.gap = GAP_MAX_M
        self.t = 0.0
        self.events = []
        self.ever_held = set()
        self.drops = 0
        self._tcp_prev = self.arm.tcp(self.q)

    # ------------------------------------------------------------- queries
    def cup(self, label):
        return next(c for c in self.cups if c.label == label)

    def grip(self):
        return self.gap / GAP_MAX_M

    def held(self):
        return next((c for c in self.cups if c.state == 'held'), None)

    def _event(self, kind, **info):
        info.update(kind=kind, t=round(self.t, 3))
        self.events.append(info)

    # ------------------------------------------------------------- dynamics
    def step(self, q_target, grip_target):
        q_target = self.arm.clip(q_target)
        grip_target = float(min(max(grip_target, 0.0), 1.0))
        h = DT / SUBSTEPS
        for _ in range(SUBSTEPS):
            q_prev, gap_prev = self.q.copy(), self.gap
            self.q = self.arm.clip(self.q + np.clip(q_target - self.q, -JOINT_SPEED * h, JOINT_SPEED * h))
            want = grip_target * GAP_MAX_M
            self.gap += float(np.clip(want - self.gap, -GAP_SPEED * h, GAP_SPEED * h))
            self.t += h
            if not self._resolve(q_prev, gap_prev, want, h):
                self.q, self.gap = q_prev, max(self.gap, gap_prev)

    def _resolve(self, q_prev, gap_prev, gap_want, h):
        """Contacts after one substep; False rejects the arm move (blocked)."""
        p, R = self.arm.tcp(self.q)
        p0, R0 = self._tcp_prev
        tool_speed = float(np.linalg.norm(p - p0)) / h
        x_jaw, z_app = R[:, 0], R[:, 2]
        palm = p - PALM_BACK * z_app
        if p[2] < 0.005 or palm[2] < 0.0:
            self._event('collision', what='tabletop')
            return False
        held = self.held()
        if held is not None:
            if gap_want > self.gap + 1e-9 and self.gap > self._diameter_at_tcp(held, p) + 0.008:
                self._release(held)
            elif tool_speed > SLIP_SPEED:
                self._event('drop', cup=held.label, why='slip')
                self._release(held)
            else:
                self.gap = max(self.gap, self._diameter_at_tcp(held, p))
                c_prev = held.center.copy()
                held.center = p + R @ held.rel[0]
                held.axis = R @ held.rel[1]
                held.speed = float(np.linalg.norm(held.center - c_prev)) / h
                if held.lowest_clearance() < -0.002:
                    self._event('collision', what='tabletop', cup=held.label)
                    held.center, held.axis = c_prev, R0 @ held.rel[1]
                    return False
        for cup in self.cups:
            if cup.state != 'standing':
                if cup.state == 'fallen':
                    cup.speed = 0.0
                continue
            cup.speed = 0.0
            rel = p - cup.center
            z_rel = p[2] - (cup.center[2] - CUP_HEIGHT / 2)
            radius = cup_radius(z_rel)
            # Enclosed: cup axis on the jaw line, between the open jaws.
            off = rel - np.dot(rel, x_jaw) * x_jaw
            off[2] = 0.0
            enclosed = (held is None and 0.0 <= z_rel <= CUP_HEIGHT and np.linalg.norm(off) < 0.015
                        and abs(float(np.dot(rel, x_jaw))) < self.gap / 2)
            if enclosed and self.gap <= 2 * radius + 0.002 and gap_prev > self.gap:
                self.gap = 2 * radius
                cup.state = 'held'
                cup.rel = (R.T @ (cup.center - p), R.T @ cup.axis)
                self.ever_held.add(cup.label)
                self._event('grasp', cup=cup.label)
                held = cup
                continue
            # Palm, or jaw tips that are not wrapping this cup, knock it over.
            parts = [(palm, 0.012, 'palm')]
            if not enclosed:
                parts += [(p + (self.gap / 2) * x_jaw, 0.0, 'jaw'), (p - (self.gap / 2) * x_jaw, 0.0, 'jaw')]
            for point, margin, part in parts:
                zr = point[2] - (cup.center[2] - CUP_HEIGHT / 2)
                if 0.0 <= zr <= CUP_HEIGHT and np.linalg.norm((point - cup.center)[:2]) < cup_radius(zr) + margin:
                    self._knock(cup, part)
                    break
            if cup.state == 'standing' and held is not None:
                d = np.linalg.norm((held.center - cup.center)[:2])
                if d < CUP_RIM_R * 2 and abs(held.center[2] - cup.center[2]) < CUP_HEIGHT:
                    self._knock(cup, 'held cup')
        self._tcp_prev = (p, R)
        return True

    def _diameter_at_tcp(self, cup, p):
        bottom = cup.center - cup.axis * CUP_HEIGHT / 2
        return 2 * cup_radius(float(np.dot(p - bottom, cup.axis)))

    def _knock(self, cup, part):
        cup.state = 'fallen'
        cup.axis = np.array([1.0, 0.0, 0.0])
        cup.center = np.array([cup.center[0], cup.center[1], CUP_RIM_R])
        self._event('collision', what='cup', cup=cup.label, part=part)

    def _release(self, cup):
        clearance = cup.lowest_clearance()
        cup.rel = None
        if clearance > 0.02 or cup.tilt_deg() > 20:
            cup.state = 'fallen'
            cup.axis = np.array([1.0, 0.0, 0.0])
            cup.center = np.array([cup.center[0], cup.center[1], CUP_RIM_R])
            self.drops += 1
            self._event('drop', cup=cup.label, why='released above the table')
        else:
            cup.state = 'standing'
            cup.axis = np.array([0.0, 0.0, 1.0])
            cup.center = np.array([cup.center[0], cup.center[1], CUP_HEIGHT / 2])
            self._event('release', cup=cup.label)

    # ------------------------------------------------------------- evidence
    def evidence(self, label=None):
        """Recorder-shaped cup evidence (docs/dream-demonstration-recorder.md)."""
        cup = self.cup(label or self.target)
        on_table = cup.state != 'held' and cup.lowest_clearance() < 0.005
        return {
            'physics_simulated': False,      # kinematic bench, never Isaac/Chaos
            'gravity_enabled': True,
            'attached_to': None,             # friction proxy, no attachment
            'support_snapshot_complete': True,
            'bilateral_jaw_contacts': cup.state == 'held',
            'other_support_contacts': ['tabletop'] if on_table else [],
            'lowest_point_clearance_cm': round(cup.lowest_clearance() * 100, 3),
            'tilt_degrees': round(cup.tilt_deg(), 3),
            'speed_cm_s': round(cup.speed * 100, 3),
            'state': cup.state,
        }

    def observe(self, cup_estimate):
        """Policy observation; cup_estimate stands in for overhead-camera detection."""
        p, _ = self.arm.tcp(self.q)
        est = np.asarray(cup_estimate, dtype=float)
        return np.r_[self.q, self.grip(), p, est - p, est[2]].astype(np.float32)

    def raw_state(self):
        """Recorder-style joint packet; the right arm holds the fleet home pose."""
        return {'q_left': self.q.tolist(), 'q_right': HOME.tolist(),
                'gap_left_m': float(self.gap), 'gap_right_m': GAP_MAX_M * 0.95}


OBS_NAMES = (['q%d' % i for i in range(1, 7)] + ['grip'] + ['tcp_x', 'tcp_y', 'tcp_z']
             + ['cup_dx', 'cup_dy', 'cup_dz', 'cup_z'])
ACTION_NAMES = ['dq%d' % i for i in range(1, 7)] + ['grip_target']


def clone(table):
    """Cheap copy for look-ahead planning; shares the stateless Arm."""
    import copy
    arm, table.arm = table.arm, None
    try:
        twin = copy.deepcopy(table)
    finally:
        table.arm = arm
    twin.arm = arm
    return twin
