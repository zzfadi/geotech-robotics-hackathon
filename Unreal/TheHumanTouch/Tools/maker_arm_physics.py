"""Derive Unreal physics inputs from the pinned URDF; no hardware commands.

Geometry is link-local RH centimetres for UE's OBJ importer (which reflects Y).
Numeric physics data is already reflected into UE link coordinates. Do not apply
another Y reflection or the station's world mounting rotation to this data.
"""
import hashlib
import json
import math
from pathlib import Path
from maker_arm_model import MODEL, model, numbers, origin, prepare

H = [1, -1, 1]


def matmul(a, b):
    return [[sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def transpose(a):
    return [list(row) for row in zip(*a)]


def principal_inertia(tensor):
    """Jacobi eigensystem of a symmetric inertia tensor; columns are mass axes."""
    a = [row[:] for row in tensor]
    v = [[float(i == j) for j in range(3)] for i in range(3)]
    for _ in range(64):
        p, q = max(((0,1),(0,2),(1,2)), key=lambda ij: abs(a[ij[0]][ij[1]]))
        if abs(a[p][q]) <= 1e-13 * max(1., max(abs(a[i][i]) for i in range(3))):
            break
        theta = .5 * math.atan2(2*a[p][q], a[q][q]-a[p][p])
        c, s = math.cos(theta), math.sin(theta)
        r = [[float(i == j) for j in range(3)] for i in range(3)]
        r[p][p] = r[q][q] = c
        r[p][q], r[q][p] = s, -s
        a = matmul(matmul(transpose(r), a), r)
        v = matmul(v, r)
    else:
        raise ValueError('Inertia diagonalization did not converge')
    order = sorted(range(3), key=lambda i:a[i][i])
    values = [a[i][i] for i in order]
    axes = [[row[i] for i in order] for row in v]
    det = sum(axes[0][i]*(axes[1][(i+1)%3]*axes[2][(i+2)%3]-axes[1][(i+2)%3]*axes[2][(i+1)%3]) for i in range(3))
    if det < 0:
        for row in axes: row[2] *= -1
    if values[0] <= 0 or values[2] > values[0]+values[1]+1e-8:
        raise ValueError('Nonphysical principal moments')
    return values, axes


def inertial_data(node):
    mass = float(node.find('mass').get('value'))
    if not math.isfinite(mass) or mass <= 0:
        raise ValueError('Expected positive mass')
    t = origin(node.find('origin'))
    inertia = node.find('inertia')
    raw = [[float(inertia.get('i'+''.join(sorted(axes)))) for axes in (a+'x',a+'y',a+'z')] for a in 'xyz']
    if not all(math.isfinite(x) for row in raw for x in row):
        raise ValueError('Nonfinite inertia')
    if not all(math.isfinite(x) for row in t for x in row):
        raise ValueError("Nonfinite inertial origin")
    r = [row[:3] for row in t[:3]]
    tensor = matmul(matmul(r, raw), transpose(r))
    tensor = [[10000*H[i]*H[j]*tensor[i][j] for j in range(3)] for i in range(3)]
    values, axes = principal_inertia(tensor)
    return {'mass_kg':mass, 'com_cm':[100*H[i]*t[i][3] for i in range(3)],
            'inertia_kg_cm2':tensor, 'principal_moments_kg_cm2':values,
            'principal_axes_columns':axes}


def description():
    provenance=json.loads((MODEL.parent/'provenance.json').read_text())
    for name,digest in provenance['sha256'].items():
        if hashlib.sha256((MODEL.parent/name).read_bytes()).hexdigest()!=digest:
            raise ValueError('Pinned source changed: '+name)
    root = model()
    bodies = {}
    frames = {}
    for link in root.findall('link'):
        name = link.get('name')
        node = link.find('inertial')
        if node is None:
            if link.findall('collision'): raise ValueError('Collision link has no inertia')
            continue
        body = inertial_data(node)
        body.update(collision_parts=len(link.findall('collision')),
                    visual_parts=len(link.findall('visual')),
                    collision_mesh='SM_MakerArm_Collision_'+name,
                    visual_mesh='SM_MakerArm_'+name)
        bodies[name] = body
    joints=[]
    for j in root.findall('joint'):
        kind = j.get('type')
        t = origin(j.find('origin'))
        row={'name':j.get('name'), 'kind':kind,
             'parent':j.find('parent').get('link'), 'child':j.find('child').get('link'),
             'origin_cm':[100*H[i]*t[i][3] for i in range(3)],
             'rotation':[[H[i]*H[k]*t[i][k] for k in range(3)] for i in range(3)]}
        if kind=='fixed':
            frames[row['child']]=row
            continue
        if kind not in ('revolute','prismatic'): raise ValueError('Unsupported joint')
        axis=numbers(j.find('axis').get('xyz'))
        norm=math.sqrt(sum(x*x for x in axis))
        if not math.isfinite(norm) or norm == 0: raise ValueError('Invalid axis')
        # Angular axes are axial vectors; translation axes are polar vectors.
        row['axis']=[H[i]*axis[i]/norm*(-1 if kind=='revolute' else 1) for i in range(3)]
        limit=j.find('limit')
        distance=1 if kind=='revolute' else 100
        effort=10000 if kind=='revolute' else 100
        row['coordinate_unit']='radian' if kind=='revolute' else 'cm'
        row['effort_unit']='kg_cm2_s2' if kind=='revolute' else 'kg_cm_s2'
        row['limits']={k:float(limit.get(k))*distance for k in ('lower','upper','velocity')}
        row['limits']['effort']=float(limit.get('effort'))*effort
        dynamics=j.find('dynamics')
        row['damping']=(float(dynamics.get('damping','0')) if dynamics is not None else 0)*(10000 if kind=='revolute' else 1)
        row['friction']=(float(dynamics.get('friction','0')) if dynamics is not None else 0)*effort
        mimic=j.find('mimic')
        if mimic is not None:
            row['mimic']={'joint':mimic.get('joint'), 'multiplier':float(mimic.get('multiplier','1')),
                          'offset':float(mimic.get('offset','0'))*distance}
        joints.append(row)
    return {'schema':1, 'robot':root.get('name'),
            'urdf_sha256':hashlib.sha256(MODEL.read_bytes()).hexdigest(),
            'upstream_commit':provenance['commit'],
            'units':{'length':'cm','mass':'kg','time':'s','angle':'radian'},
            'coordinates':'UE link-local; Y reflected; station mounting applied separately',
            'validation':'upstream estimated dynamics; contact and hardware calibration unvalidated',
            'bodies':bodies, 'joints':joints, 'fixed_frames':frames,
            'total_mass_kg':sum(x['mass_kg'] for x in bodies.values())}


def prepare_physics(destination=None):
    folder=Path(destination or MODEL.parents[2]/'Saved/CoPresenceImports/MakerArmPhysics')
    counts=prepare(folder, geometry='collision')
    result=description()
    for name,count in counts.items(): result['bodies'][name]['collision_triangles']=count
    (folder/'physics.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    return result

if __name__=='__main__':
    result=prepare_physics()
    print(json.dumps({'bodies':len(result['bodies']),'joints':len(result['joints']),
                      'mass_kg':result['total_mass_kg']},indent=2))
