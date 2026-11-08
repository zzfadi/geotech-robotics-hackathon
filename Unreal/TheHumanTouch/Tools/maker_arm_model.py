"""Pinned Maker Arm geometry and kinematics; no validated dynamics or hardware control."""
import math
from functools import lru_cache
import struct
from pathlib import Path
import xml.etree.ElementTree as ET
from yam_kinematics import identity, mul, numbers, origin, rotation, ue_pose

MODEL = Path(__file__).resolve().parents[1] / 'SourceAssets/MakerArm/robot.urdf'

def model():
    return ET.parse(MODEL).getroot()

@lru_cache(maxsize=1)
def _frames_model(path, mtime_ns, size):
    # Private read-only root; public model() deliberately returns a fresh tree.
    return ET.parse(path).getroot()

@lru_cache(maxsize=1)
def _joint_plan(root):
    """Joints of one cached URDF tree, parsed to numbers once, in exactly the
    order the breadth-first traversal visits them (keyed by tree identity)."""
    joints = root.findall('joint')
    revolute = [j.get('name') for j in joints if j.get('type') == 'revolute']
    order, known, pending = [], {'base_link'}, list(joints)
    while pending:
        ready = [j for j in pending if j.find('parent').get('link') in known]
        if not ready:
            raise ValueError('Disconnected or cyclic URDF')
        for j in ready:
            kind = j.get('type')
            mimic = limits = axis = None
            if kind != 'fixed':
                m = j.find('mimic')
                if m is not None:
                    mimic = (m.get('joint'), float(m.get('multiplier', '1')), float(m.get('offset', '0')))
                lim = j.find('limit')
                limits = (float(lim.get('lower')), float(lim.get('upper')))
                axis = numbers(j.find('axis').get('xyz'))
                length = math.sqrt(sum(v*v for v in axis))
                axis = [v / length for v in axis]
            order.append((j.get('name'), kind, j.find('parent').get('link'), j.find('child').get('link'),
                          origin(j.find('origin')), mimic, limits, axis))
            known.add(j.find('child').get('link'))
            pending.remove(j)
    return revolute, order

def frames(q, gap=0.04):
    """Six radians plus total jaw gap in metres, in original CAD coordinates."""
    if len(q) != 6 or not all(math.isfinite(v) for v in [*q, gap]):
        raise ValueError('Expected six finite radians and a finite jaw gap in metres')
    stat = MODEL.stat()
    revolute, order = _joint_plan(_frames_model(str(MODEL.resolve()), stat.st_mtime_ns, stat.st_size))
    values = dict(zip(revolute, q))
    values['gripper_left_joint'] = gap / 2
    out = {'base_link': identity()}
    for name, kind, parent, child, joint_origin, mimic, limits, axis in order:
        delta = identity()
        if kind != 'fixed':
            v = values[name] if mimic is None else values[mimic[0]] * mimic[1] + mimic[2]
            if not limits[0] <= v <= limits[1]:
                raise ValueError(f'{name} outside model limit: {v}')
            if kind == 'revolute':
                delta = rotation(axis, v)
            elif kind == 'prismatic':
                for i in range(3): delta[i][3] = axis[i] * v
            else:
                raise ValueError(f'Unsupported joint {kind}')
        out[child] = mul(mul(out[parent], joint_origin), delta)
    return out

def mounted_frames(q, gap=0.04, yaw=0.0):
    """Rotate CAD Y-up onto world Z-up, as documented by upstream."""
    if not math.isfinite(yaw):
        raise ValueError("Mount yaw must be finite")
    mount = mul(rotation([0, 0, 1], yaw), rotation([1, 0, 0], math.pi / 2))
    return {name: mul(mount, t) for name, t in frames(q, gap).items()}

def prepare(destination=None, geometry="visual"):
    """Combine visual or collision parts per link, preserving origins and scale."""
    if geometry not in ("visual", "collision"):
        raise ValueError("Expected visual or collision geometry")
    destination = Path(destination or MODEL.parents[2] / 'Saved/CoPresenceImports/MakerArm')
    destination.mkdir(parents=True, exist_ok=True)
    result = {}
    for link in model().findall('link'):
        visuals = link.findall(geometry)
        if not visuals:
            continue
        count = 0
        with (destination / (link.get('name') + '.obj')).open('w') as f:
            f.write('# MakerMods Apache-2.0; RH centimetres, link-local. UE OBJ import reflects Y.\n')
            for visual in visuals:
                mesh = visual.find('geometry/mesh')
                t = origin(visual.find('origin'))
                scale = numbers(mesh.get('scale', '1 1 1'))
                data = (MODEL.parent / mesh.get('filename')).read_bytes()
                triangles = struct.unpack_from('<I', data, 80)[0]
                if len(data) != 84 + 50 * triangles:
                    raise ValueError('Expected binary STL')
                for n in range(triangles):
                    v = struct.unpack_from('<12f', data, 84 + 50*n)
                    for k in range(3):
                        p = [v[3+3*k+i] * scale[i] for i in range(3)]
                        p = [100 * (sum(t[i][j]*p[j] for j in range(3)) + t[i][3]) for i in range(3)]
                        f.write('v ' + ' '.join(f'{x:.9g}' for x in p) + '\n')
                    f.write(f'f {3*count+1} {3*count+2} {3*count+3}\n')
                    count += 1
        result[link.get('name')] = count
    return result

if __name__ == '__main__':
    print(prepare())
