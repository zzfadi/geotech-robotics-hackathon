"""Pinned Maker Arm geometry and kinematics; no validated dynamics or hardware control."""
import math
import struct
from pathlib import Path
import xml.etree.ElementTree as ET
from yam_kinematics import identity, mul, numbers, origin, rotation, ue_pose

MODEL = Path(__file__).resolve().parents[1] / 'SourceAssets/MakerArm/robot.urdf'

def model():
    return ET.parse(MODEL).getroot()

def frames(q, gap=0.04):
    """Six radians plus total jaw gap in metres, in original CAD coordinates."""
    if len(q) != 6 or not all(math.isfinite(v) for v in [*q, gap]):
        raise ValueError('Expected six finite radians and a finite jaw gap in metres')
    r = model()
    joints = r.findall('joint')
    values = {j.get('name'): v for j, v in zip([j for j in joints if j.get('type') == 'revolute'], q)}
    values['gripper_left_joint'] = gap / 2
    out = {'base_link': identity()}
    pending = list(joints)
    while pending:
        ready = [j for j in pending if j.find('parent').get('link') in out]
        if not ready:
            raise ValueError('Disconnected or cyclic URDF')
        for j in ready:
            kind = j.get('type')
            delta = identity()
            if kind != 'fixed':
                mimic = j.find('mimic')
                v = values[j.get('name')] if mimic is None else values[mimic.get('joint')] * float(mimic.get('multiplier', '1')) + float(mimic.get('offset', '0'))
                lim = j.find('limit')
                if not float(lim.get('lower')) <= v <= float(lim.get('upper')):
                    raise ValueError(f'{j.get("name")} outside model limit: {v}')
                axis = numbers(j.find('axis').get('xyz'))
                length = math.sqrt(sum(v*v for v in axis))
                axis = [v / length for v in axis]
                if kind == 'revolute':
                    delta = rotation(axis, v)
                elif kind == 'prismatic':
                    for i in range(3): delta[i][3] = axis[i] * v
                else:
                    raise ValueError(f'Unsupported joint {kind}')
            out[j.find('child').get('link')] = mul(mul(out[j.find('parent').get('link')], origin(j.find('origin'))), delta)
            pending.remove(j)
    return out

def mounted_frames(q, gap=0.04):
    """Rotate CAD Y-up onto world Z-up, as documented by upstream."""
    mount = rotation([1, 0, 0], math.pi / 2)
    return {name: mul(mount, t) for name, t in frames(q, gap).items()}

def prepare(destination=None):
    """Combine every visual per link, preserving origins and STL millimetre scale."""
    destination = Path(destination or MODEL.parents[2] / 'Saved/CoPresenceImports/MakerArm')
    destination.mkdir(parents=True, exist_ok=True)
    result = {}
    for link in model().findall('link'):
        visuals = link.findall('visual')
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
