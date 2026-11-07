"""Explicit simulation-only local grasp servo after a Dream approach.
Reads the known test prop pose; writes only arm targets, never prop transforms.
This is privileged-state assistance, not an unmodified Dream policy result.
"""
import math,sys
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Unreal/TheHumanTouch/Tools'))
from maker_arm_model import model,mounted_frames

TCP=np.array([0.,0.,-.169638704618])
ROT=Rotation.from_euler('y',math.radians(-80)).as_matrix()
TARGET='MakerStation_Approximate_Block_Red'
PHASES=('open and align above target','descend around target','close and settle','lift and hold')

def solve(position,q):
    limits=[j.find('limit') for j in model().findall("joint[@type='revolute']")]
    bounds=([float(j.get('lower'))+1e-6 for j in limits],[float(j.get('upper'))-1e-6 for j in limits])
    def error(x):
        f=np.array(mounted_frames(x,0,yaw=math.pi)['link_007'])
        return np.r_[5*(f[:3,3]+f[:3,:3]@TCP-position),Rotation.from_matrix(ROT.T@f[:3,:3]).as_rotvec()]
    result=least_squares(error,q,bounds=bounds,max_nfev=150)
    e=error(result.x)
    if np.linalg.norm(e[:3])/5>.001 or np.linalg.norm(e[3:])>.01:
        raise ValueError('Local grasp target is not reachable within 1 mm / .01 rad')
    return result.x.tolist()


def action(phase,state,status):
    if phase not in range(4):raise ValueError('Unknown local grasp phase')
    prop=status['props'][TARGET]
    if prop['speed_cm_s']>5:raise ValueError('Target is moving too fast for local grasp alignment')
    # This explicit fixture is a 5x5x7 cm block at the left station.
    x,y,z=prop['position_cm']
    if not (55<x<65 and -48<y<-32 and 78<prop['initial_z_cm']<79):
        raise ValueError('Local grasp assistance requires the declared side-grasp block setup')
    q=list(state['q_left']);gap=state['gap_left_m'];rows=[q+[gap/.104825]]
    right=list(state['q_right'])+[state['gap_right_m']/.104825]
    position=np.array([(x-20)/100,-(y+40)/100,(z-76)/100])
    if phase==0:
        above=position+np.array([0,0,.10]);goal=solve(above,q)
        for t in np.linspace(0,1,40)[1:]:rows.append([(1-t)*a+t*b for a,b in zip(q,goal)]+[((1-t)*gap+t*.095)/.104825])
    elif phase==1:
        f=np.array(mounted_frames(q,0,yaw=math.pi)['link_007']);start=f[:3,3]+f[:3,:3]@TCP
        for t in np.linspace(0,1,50)[1:]:
            q=solve(start*(1-t)+position*t,q);rows.append(q+[.095/.104825])
    elif phase==2:
        for t in np.linspace(0,1,40)[1:]:rows.append(q+[((1-t)*gap+t*.04)/.104825])
        rows.extend([rows[-1][:] for _ in range(20)])
    else:
        f=np.array(mounted_frames(q,0,yaw=math.pi)['link_007']);start=f[:3,3]+f[:3,:3]@TCP
        # 11 cm commanded tool rise leaves a 1 cm allowance for contact settling.
        for t in np.linspace(0,1,60)[1:]:
            q=solve(start+np.array([0,0,.11*t]),q);rows.append(q+[min(gap,.04)/.104825])
        rows.extend([rows[-1][:] for _ in range(60)])
    return {'q_l_chunk':rows,'q_r_chunk':[right[:] for _ in rows]}
