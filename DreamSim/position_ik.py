"""Position-priority IK for Dream EEF targets on the pinned Maker Arm.
No prop state, object coordinates, task phases or scripted lift targets are used.
Orientation follows the SDK's solved orientation softly; position is constrained.
"""
import copy,math,sys
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Unreal/TheHumanTouch/Tools'))
from maker_arm_model import model,mounted_frames


def orientation_reference(q, bounds, epsilon=1e-4):
    """Correct SDK roundoff only for the soft orientation reference."""
    q=np.asarray(q,dtype=float)
    low,high=bounds
    if not np.isfinite(q).all() or np.any(q<low-epsilon) or np.any(q>high+epsilon):
        raise ValueError('Policy orientation reference outside model limits')
    return np.clip(q,low,high)


def adapt(action,state,rows=12):
    result=copy.deepcopy(action);report={}
    limits=[j.find('limit') for j in model().findall("joint[@type='revolute']")]
    bounds=(np.array([float(j.get('lower')) for j in limits])+1e-8,np.array([float(j.get('upper')) for j in limits])-1e-8)
    for side,key in [('left','q_l_chunk'),('right','q_r_chunk')]:
        xyz=np.array(action['per_key']['action.'+side+'_eef_xyz'],dtype=float)
        original=np.array(action[key],dtype=float)
        if xyz.shape!=(len(original),3) or original.ndim!=2 or original.shape[1]!=7 or not np.isfinite(xyz).all() or not np.isfinite(original).all():
            raise ValueError('Invalid policy EEF trajectory')
        count=min(rows,len(original));q=np.array(state['q_'+side],dtype=float)
        output=[q.tolist()+[float(original[0,6])]];errors=[];angles=[]
        for i in range(1,count):
            desired=np.array(mounted_frames(orientation_reference(original[i,:6],bounds),0,yaw=math.pi)['grasp_center'])[:3,:3]
            prior=q.copy()
            def residual(v):
                f=np.array(mounted_frames(v,0,yaw=math.pi)['grasp_center'])
                return np.r_[100*(f[:3,3]-xyz[i]),.1*Rotation.from_matrix(desired.T@f[:3,:3]).as_rotvec(),.01*(v-prior)]
            solved=least_squares(residual,np.clip(q,*bounds),bounds=bounds,max_nfev=100,ftol=1e-7,xtol=1e-7,gtol=1e-7)
            q=solved.x;f=np.array(mounted_frames(q,0,yaw=math.pi)['grasp_center'])
            error=float(np.linalg.norm(f[:3,3]-xyz[i]));angle=float(np.linalg.norm(Rotation.from_matrix(desired.T@f[:3,:3]).as_rotvec()))
            if error>.002:raise ValueError(f'{side} policy target unreachable within 2 mm ({error*1000:.2f} mm)')
            output.append(q.tolist()+[float(original[i,6])]);errors.append(error);angles.append(angle)
        result[key]=output
        report[side]={'max_position_error_m':max(errors,default=0.),'max_orientation_deviation_radians':max(angles,default=0.),'rows':count}
    return result,report
