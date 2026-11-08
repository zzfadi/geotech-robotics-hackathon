"""Optional MuJoCo cup-grasp check: does a physics grasp and lift actually hold?

Builds one Maker Arm from the pinned URDF and its meshes the way
Capture/contact_sim.py does (MuJoCo's URDF import with fusestatic off, then
the canonical XML), mounts it with the same Y-up to Z-up mount as
DreamSim/compiled_fk.py, and adds a tabletop plane at the base height plus
free cylinder cups at the fleet layout positions. The scripted expert (and
optionally a trained policy) drives position servos on the six joints and the
jaw at 5 Hz, reading measured joints and gap back from the simulation, and the
target cup is scored with the same gate as the bench (10 cm clearance for 5 s,
<= 15 deg tilt, <= 10 cm/s, both jaws, nothing else touching).

This is a physics-SIM result, not a physical one: cup mass, friction, servo
gains, gravity compensation on the arm links and the jaw collision hulls are
assumptions, and arm-table contact is not modelled. mujoco is optional; the
rest of edge_model stays numpy-only. Run with:

  uv run --python 3.12 --with numpy --with mujoco==4.0.0 python -m edge_model.physics_bench --seeds 2
"""
import argparse
import json
import math
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

from edge_model import gate
from edge_model.bench import CUP_HEIGHT, DT, fleet_layout
from edge_model.expert import Expert
from edge_model.kinematics import GAP_MAX_M, HOME, REPO, Arm

URDF = REPO / 'Unreal/TheHumanTouch/SourceAssets/MakerArm/robot.urdf'
SIM_DT = 0.002
CONE, IMPRATIO = 'elliptic', 10   # standard MuJoCo grasp settings: less creep under soft contacts
CUP_R = 0.035            # the bench cup is 3 cm at the base, 4 cm at the rim; 3.5 cm at grasp height
CUP_MASS = 0.05          # kg, an empty-ish cup (assumption)
FRICTION = 1.0           # sliding friction (assumption)
ARM_KP, ARM_KV, ARM_FORCE = 300.0, 15.0, 10.0      # URDF effort limit 10 N m
JAW_KP, JAW_KV, JAW_FORCE = 400.0, 10.0, 20.0      # URDF effort limit 20 N
ARM_JOINTS = ['link_%03d_joint' % i for i in range(2, 8)]


def _mount_quat():
    from compiled_fk import ArmFK  # on sys.path via edge_model.kinematics
    R = ArmFK().mount[:3, :3]
    w = math.sqrt(max(0.0, 1 + R[0, 0] + R[1, 1] + R[2, 2])) / 2
    if w < 1e-6:
        # 180-degree rotation: recover from the diagonal
        x = math.sqrt(max(0.0, (1 + R[0, 0] - R[1, 1] - R[2, 2]) / 4))
        y = math.sqrt(max(0.0, (1 - R[0, 0] + R[1, 1] - R[2, 2]) / 4))
        z = math.sqrt(max(0.0, (1 - R[0, 0] - R[1, 1] + R[2, 2]) / 4))
        x = math.copysign(x, R[2, 1] - R[1, 2]) if x else x
        y = math.copysign(y, R[0, 2] - R[2, 0]) if y else y
        z = math.copysign(z, R[1, 0] - R[0, 1]) if z else z
        if x and y and R[0, 1] + R[1, 0] < 0:
            y = -y
        return [w, x, y, z]
    return [w, (R[2, 1] - R[1, 2]) / (4 * w), (R[0, 2] - R[2, 0]) / (4 * w), (R[1, 0] - R[0, 1]) / (4 * w)]


def build(cups, cup_mass=CUP_MASS, friction=FRICTION):
    import mujoco
    xml = ET.parse(URDF).getroot()
    ET.SubElement(ET.SubElement(xml, 'mujoco'), 'compiler', fusestatic='false')
    assets = {f.name: f.read_bytes() for f in (URDF.parent / 'meshes').glob('*.stl')}
    single = mujoco.MjModel.from_xml_string(ET.tostring(xml, encoding='unicode'), assets)
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / 'canonical.xml')
        mujoco.mj_saveLastXML(path, single)
        scene = ET.parse(path).getroot()
    ET.SubElement(scene, 'option', timestep=str(SIM_DT), integrator='implicitfast', cone=CONE, impratio=str(IMPRATIO))
    world = scene.find('worldbody')
    base = world.find('body')
    base.set('pos', '0 0 0')
    base.set('quat', ' '.join('%.12g' % v for v in _mount_quat()))
    for body in base.iter('body'):
        body.set('gravcomp', '1')          # servos hold the arm against gravity (assumption)
    for geom in base.iter('geom'):
        geom.set('contype', '1')
        geom.set('conaffinity', '2')        # arm touches cups only; no self or table contact
        geom.set('friction', '%g 0.005 0.0001' % friction)
        geom.set('condim', '4')
    for joint in base.iter('joint'):
        joint.set('damping', '0.5')
        joint.set('armature', '0.01')
    ET.SubElement(world, 'geom', name='table', type='plane', size='2 2 .02', pos='0 0 0',
                  contype='4', conaffinity='2', friction='%g 0.005 0.0001' % friction)
    for i, cup in enumerate(cups):
        b = ET.SubElement(world, 'body', name='cup%d' % i, pos='%g %g %g' % (cup['xy'][0], cup['xy'][1], CUP_HEIGHT / 2))
        ET.SubElement(b, 'freejoint', name='cup%d_free' % i)
        ET.SubElement(b, 'geom', name='cup%d_geom' % i, type='cylinder', size='%g %g' % (CUP_R, CUP_HEIGHT / 2),
                      mass=str(cup_mass), contype='2', conaffinity='7', condim='4',
                      friction='%g 0.005 0.0001' % friction)
    act = ET.SubElement(scene, 'actuator')
    for j in ARM_JOINTS:
        ET.SubElement(act, 'position', name='a_' + j, joint=j, kp=str(ARM_KP), kv=str(ARM_KV),
                      forcerange='%g %g' % (-ARM_FORCE, ARM_FORCE))
    ET.SubElement(act, 'position', name='a_jaw', joint='gripper_left_joint', kp=str(JAW_KP), kv=str(JAW_KV),
                  forcerange='%g %g' % (-JAW_FORCE, JAW_FORCE))
    return mujoco.MjModel.from_xml_string(ET.tostring(scene, encoding='unicode'), assets)


class _Held:
    label = 'measured'


class PhysTable:
    """Adapter so the bench expert runs on MuJoCo: measured joints and gap in, servo targets out."""

    def __init__(self, table_index, seed, cup_mass=CUP_MASS, friction=FRICTION):
        import mujoco
        self.mujoco = mujoco
        self.layout = fleet_layout(table_index, seed)
        self.model = build(self.layout, cup_mass, friction)
        self.data = mujoco.MjData(self.model)
        self.arm = Arm()
        m = self.model
        self.qadr = [m.joint(j).qposadr[0] for j in ARM_JOINTS]
        self.jaw = m.joint('gripper_left_joint').qposadr[0]
        self.jaw_r = m.joint('gripper_right_joint').qposadr[0]
        for a, v in zip(self.qadr, HOME):
            self.data.qpos[a] = v
        self.data.ctrl[:6] = HOME
        self.data.qpos[self.jaw] = self.data.qpos[self.jaw_r] = GAP_MAX_M / 2
        self.data.ctrl[6] = GAP_MAX_M / 2
        mujoco.mj_forward(m, self.data)
        self.cup_body = m.body('cup0').id
        self.cup_geom = m.geom('cup0_geom').id
        self.table_geom = m.geom('table').id
        self.left_jaw_body = m.body('gripper_left').id
        self.right_jaw_body = m.body('gripper_right').id
        self.t = 0.0
        self.grip_cmd = 1.0
        self.closed_for = 0.0
        self.target = 'cup0'
        self.ever_held = set()
        self.events = []
        self.drops = 0
        self._q_cmd = HOME.copy()

    @property
    def q(self):
        return self.arm.clip(np.array([self.data.qpos[a] for a in self.qadr]))

    @property
    def gap(self):
        return float(self.data.qpos[self.jaw] + self.data.qpos[self.jaw_r])

    def held(self):
        # What the arm itself can measure: jaws commanded shut for 0.4 s and stopped on something.
        return _Held() if self.grip_cmd < 0.5 and self.closed_for >= 0.4 and self.gap > 0.045 else None

    def cup(self, label):
        class _C:
            pass
        c = _C()
        c.center = np.array(self.data.xpos[self.cup_body])
        return c

    def step(self, q_target, grip):
        q_target = self.arm.clip(q_target)
        grip = float(min(max(grip, 0.0), 1.0))
        n = int(round(DT / SIM_DT))
        start = self._q_cmd.copy()
        for i in range(n):
            self.data.ctrl[:6] = start + (q_target - start) * (i + 1) / n
            self.data.ctrl[6] = grip * GAP_MAX_M / 2
            self.mujoco.mj_step(self.model, self.data)
        self._q_cmd = q_target
        self.t += DT
        self.closed_for = self.closed_for + DT if grip < 0.5 else 0.0
        self.grip_cmd = grip

    def contacts(self):
        m, d = self.model, self.data
        jaws, others = set(), []
        for i in range(d.ncon):
            c = d.contact[i]
            g1, g2 = c.geom1, c.geom2
            if self.cup_geom not in (g1, g2):
                continue
            other = g2 if g1 == self.cup_geom else g1
            body = m.geom_bodyid[other]
            if body == self.left_jaw_body:
                jaws.add('left')
            elif body == self.right_jaw_body:
                jaws.add('right')
            else:
                others.append(m.geom(other).name or m.body(body).name)
        return jaws, others

    def evidence(self):
        d = self.data
        R = d.xmat[self.cup_body].reshape(3, 3)
        axis = R[:, 2]
        tilt = math.degrees(math.acos(max(-1.0, min(1.0, float(axis[2])))))
        center = d.xpos[self.cup_body]
        lowest = float(center[2] - (CUP_HEIGHT / 2) * abs(axis[2]) - CUP_R * math.sqrt(max(0.0, 1 - axis[2] ** 2)))
        vel = np.zeros(6)
        self.mujoco.mj_objectVelocity(self.model, d, self.mujoco.mjtObj.mjOBJ_BODY, self.cup_body, vel, 0)
        jaws, others = self.contacts()
        if jaws == {'left', 'right'}:
            self.ever_held.add('cup0')
        return {
            'physics_simulated': True, 'gravity_enabled': True, 'attached_to': None,
            'support_snapshot_complete': True, 'bilateral_jaw_contacts': jaws == {'left', 'right'},
            'other_support_contacts': sorted(set(others)),
            'lowest_point_clearance_cm': round(lowest * 100, 3), 'tilt_degrees': round(tilt, 3),
            'speed_cm_s': round(float(np.linalg.norm(vel[3:])) * 100, 3),
        }


def check_fk(samples=5, seed=0):
    """Max distance between MuJoCo's grasp_center and the repo FK over random joint sets."""
    import mujoco
    tb = PhysTable(0, 1000)
    rng = np.random.default_rng(seed)
    gc = tb.model.body('grasp_center').id
    worst = 0.0
    for _ in range(samples):
        q = tb.arm.low + rng.random(6) * (tb.arm.high - tb.arm.low)
        for a, v in zip(tb.qadr, q):
            tb.data.qpos[a] = v
        mujoco.mj_kinematics(tb.model, tb.data)
        worst = max(worst, float(np.linalg.norm(tb.data.xpos[gc] - tb.arm.tcp(q)[0])))
    return worst


def run(table_index, seed, driver='expert', policy=None, steps=150, cup_mass=CUP_MASS, friction=FRICTION):
    tb = PhysTable(table_index, seed, cup_mass, friction)
    est = tb.cup('cup0').center.copy()
    est[2] = CUP_HEIGHT / 2
    expert = Expert(est)
    times, evidence, phases = [], [], []
    for step in range(steps):
        if driver == 'expert':
            q, g, phase = expert.act(tb)
        else:
            p, _ = tb.arm.tcp(tb.q)
            obs = np.r_[tb.q, tb.gap / GAP_MAX_M, p, est - p, est[2]].astype(np.float32)
            dq, gp = policy.predict(obs)
            q, g, phase = tb.q + dq, (1.0 if float(gp) > 0.5 else 0.0), 'model'
        tb.step(q, g)
        phases.append(phase)
        times.append(round((step + 1) * DT, 6))
        evidence.append(tb.evidence())
        if any(e['lowest_point_clearance_cm'] < -5 for e in evidence[-1:]):
            break
    r = gate.evaluate(times, evidence)
    peak = max(e['lowest_point_clearance_cm'] for e in evidence)
    both = sum(e['bilateral_jaw_contacts'] for e in evidence) * DT
    final = evidence[-1]
    return {'table': table_index, 'seed': seed, 'driver': driver, 'measured_pass': r['measured_pass'],
            'longest_hold_s': r['longest_hold_s'], 'passed_at_s': r['passed_at_s'], 'reasons': r['reasons'],
            'max_clearance_cm': round(peak, 2), 'bilateral_contact_s': round(both, 1),
            'final_clearance_cm': final['lowest_point_clearance_cm'], 'final_tilt_deg': final['tilt_degrees'],
            'phases_seen': sorted(set(phases)), 'physics_simulated': True, 'dream_export_eligible': False,
            'cup_mass_kg': cup_mass, 'friction': friction}


def main(argv=None):
    global CONE, IMPRATIO
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--seeds', type=int, default=2)
    p.add_argument('--seed-start', type=int, default=1080)
    p.add_argument('--tables', type=int, nargs='*', default=list(range(9)))
    p.add_argument('--policy', help='optional trained checkpoint (.npz) to run as well')
    p.add_argument('--out', help='write the results JSON here')
    p.add_argument('--cone', choices=('elliptic', 'pyramidal'), default=CONE,
                   help='friction cone; MuJoCo default pyramidal with impratio 1 lets the cup creep down')
    p.add_argument('--impratio', type=float, default=IMPRATIO)
    a = p.parse_args(argv)
    CONE, IMPRATIO = a.cone, a.impratio
    t0 = time.time()
    fk = check_fk()
    print('FK check: max |grasp_center - repo FK| = %.2e m' % fk, file=sys.stderr)
    drivers = [('expert', None)]
    if a.policy:
        from edge_model.model import Policy
        drivers.append(('model', Policy.load(a.policy)))
    results = []
    for seed in range(a.seed_start, a.seed_start + a.seeds):
        for t in a.tables:
            for name, pol in drivers:
                r = run(t, seed, name, pol)
                results.append(r)
                print(json.dumps(r), file=sys.stderr, flush=True)
    import mujoco
    summary = {'kind': 'MuJoCo physics simulation (not physical)', 'mujoco': mujoco.__version__,
               'fk_max_error_m': fk, 'seconds': round(time.time() - t0, 1),
               'assumptions': {'cup_radius_m': CUP_R, 'cup_height_m': CUP_HEIGHT, 'cup_mass_kg': CUP_MASS,
                               'friction': FRICTION, 'arm_servo': [ARM_KP, ARM_KV, ARM_FORCE],
                               'jaw_servo': [JAW_KP, JAW_KV, JAW_FORCE], 'arm_gravcomp': True,
                               'arm_table_contact': False, 'timestep_s': SIM_DT, 'cone': CONE, 'impratio': IMPRATIO},
               'by_driver': {}}
    for name, _ in drivers:
        rs = [r for r in results if r['driver'] == name]
        summary['by_driver'][name] = {
            'attempts': len(rs), 'gate_pass': sum(r['measured_pass'] for r in rs),
            'longest_hold_s_median': float(np.median([r['longest_hold_s'] for r in rs])),
            'max_clearance_cm_median': float(np.median([r['max_clearance_cm'] for r in rs])),
            'reasons': {k: sum(1 for r in rs if (r['reasons'] or ['passed'])[0] == k)
                        for k in {(r['reasons'] or ['passed'])[0] for r in rs}}}
    summary['results'] = results
    if a.out:
        with open(a.out, 'w') as fh:
            json.dump(summary, fh, indent=2)
    print(json.dumps(summary['by_driver'], indent=2))


if __name__ == '__main__':
    main()
