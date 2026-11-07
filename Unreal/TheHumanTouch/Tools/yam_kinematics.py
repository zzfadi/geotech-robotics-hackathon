"""YAM URDF forward kinematics. SI/right-handed internally; UE uses cm and Y reflection."""
import math
import xml.etree.ElementTree as ET
from pathlib import Path

MODEL = Path(__file__).resolve().parents[1] / 'SourceAssets/YAM/yam.urdf'

def identity():
    return [[float(i == j) for j in range(4)] for i in range(4)]

def mul(a, b):
    return [[sum(a[i][k]*b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]

def numbers(s):
    return list(map(float, s.split()))

def rotation(axis, angle):
    x,y,z = axis
    c,s,t = math.cos(angle), math.sin(angle), 1-math.cos(angle)
    return [[t*x*x+c,t*x*y-s*z,t*x*z+s*y,0],
            [t*x*y+s*z,t*y*y+c,t*y*z-s*x,0],
            [t*x*z-s*y,t*y*z+s*x,t*z*z+c,0],[0,0,0,1]]

def origin(element):
    if element is None:
        return identity()
    r,p,y = numbers(element.get('rpy','0 0 0'))
    out = mul(mul(rotation([0,0,1],y),rotation([0,1,0],p)),rotation([1,0,0],r))
    xyz = numbers(element.get('xyz','0 0 0'))
    for i in range(3): out[i][3] = xyz[i]
    return out

def model():
    return ET.parse(MODEL).getroot()

def frames(q, finger=-0.02):
    if len(q) != 6 or not all(math.isfinite(v) for v in [*q,finger]):
        raise ValueError('Expected six finite joint angles (radians) and finger displacement (metres)')
    out = {'base':identity()}
    for j in model().findall('joint'):
        idx = int(j.get('name')[5:])-1
        v = q[idx] if idx < 6 else finger
        lim = j.find('limit')
        if not float(lim.get('lower'))-1e-9 <= v <= float(lim.get('upper'))+1e-9:
            raise ValueError(f'{j.get("name")} outside URDF position limits: {v}')
        axis = numbers(j.find('axis').get('xyz'))
        delta = rotation(axis,v) if j.get('type') == 'revolute' else identity()
        if j.get('type') == 'prismatic':
            for i in range(3): delta[i][3] = axis[i]*v
        out[j.find('child').get('link')] = mul(mul(out[j.find('parent').get('link')],origin(j.find('origin'))),delta)
    return out

def ue_pose(t):
    # S R S preserves a proper rotation; geometry separately uses S p.
    s = [1,-1,1]
    m = [[s[i]*t[i][j]*s[j] for j in range(3)] for i in range(3)]
    trace = sum(m[i][i] for i in range(3))
    if trace > 0:
        k = math.sqrt(trace+1)*2
        quat = [(m[2][1]-m[1][2])/k,(m[0][2]-m[2][0])/k,(m[1][0]-m[0][1])/k,k/4]
    else:
        i = max(range(3),key=lambda n:m[n][n]); j=(i+1)%3; k=(i+2)%3
        v = math.sqrt(1+m[i][i]-m[j][j]-m[k][k])*2
        quat=[0.,0.,0.,0.]; quat[i]=v/4; quat[j]=(m[j][i]+m[i][j])/v; quat[k]=(m[k][i]+m[i][k])/v; quat[3]=(m[k][j]-m[j][k])/v
    return [100*s[i]*t[i][3] for i in range(3)],quat
