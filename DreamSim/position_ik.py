"""Position-priority IK for Dream EEF targets on the pinned Maker Arm.
No prop state, object coordinates, task phases or scripted lift targets are used.
Orientation follows the policy's actual EEF rot6, converted from Dream TCP axes.
Legacy XYZ-only offline fixtures retain the SDK-reference path.
"""
import copy,math,sys
from pathlib import Path
import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Unreal/TheHumanTouch/Tools'))
from maker_arm_model import model,mounted_frames
from compiled_fk import ArmFK, decode_rot6


def orientation_reference(q, bounds, epsilon=1e-4):
    """Correct SDK roundoff only for the soft orientation reference."""
    q=np.asarray(q,dtype=float)
    low,high=bounds
    if not np.isfinite(q).all() or np.any(q<low-epsilon) or np.any(q>high+epsilon):
        violations=np.maximum(low-q,q-high)
        raise ValueError('Policy orientation reference outside model limits: max excess %.9g rad; q=%s' % (float(np.max(violations)),q.tolist()))
    return np.clip(q,low,high)


def adapt(action,state,rows=12,active_sides=None,allow_prefix=False):
    """Optionally return only a feasible original-target prefix for fresh replanning.

    The 2 mm tolerance is unchanged. A rejected first target still fails; no
    target is projected, replaced, skipped or executed beyond the first failure.
    Callers must bound repeated truncations and request new measured feedback.
    """
    result=copy.deepcopy(action);report={};failures=[];accepted_metrics={}
    active_sides = set(('left','right') if active_sides is None else active_sides)
    if not active_sides or not active_sides <= {'left','right'}:
        raise ValueError('Invalid active sides')
    active_key = 'q_l_chunk' if 'left' in active_sides else 'q_r_chunk'
    count = min(rows, len(action[active_key]));requested_count=count
    if allow_prefix and count<2:raise ValueError('Feasible-prefix execution requires an actionable target')
    fk=ArmFK()
    limits=[j.find('limit') for j in model().findall("joint[@type='revolute']")]
    bounds=(np.array([float(j.get('lower')) for j in limits])+1e-8,np.array([float(j.get('upper')) for j in limits])-1e-8)
    for side,key in [('left','q_l_chunk'),('right','q_r_chunk')]:
        if side not in active_sides:
            result[key]=[list(state['q_'+side])+[state['gap_'+side+'_m']/.104825] for _ in range(count)]
            report[side]={'masked':True,'reason':'explicit single-arm objective; hold observed pose','rows':count}
            continue
        xyz=np.array(action['per_key']['action.'+side+'_eef_xyz'],dtype=float)
        original=np.array(action[key],dtype=float)
        if xyz.shape!=(len(original),3) or original.ndim!=2 or original.shape[1]!=7 or not np.isfinite(xyz).all() or not np.isfinite(original).all():
            raise ValueError('Invalid policy EEF trajectory')
        rotations=action['per_key'].get('action.'+side+'_eef_rot6')
        if rotations is not None and np.asarray(rotations).shape!=(len(original),6):
            raise ValueError('Invalid policy EEF rotation trajectory')
        count=min(rows,len(original));q=np.array(state['q_'+side],dtype=float)
        output=[q.tolist()+[float(original[0,6])]];errors=[];angles=[]
        for i in range(1,count):
            desired=(decode_rot6(rotations[i])@fk.tool_axes.T if rotations is not None else
                     fk.pose(orientation_reference(original[i,:6],bounds))[:3,:3])
            prior=q.copy()
            def residual(v):
                f=fk.pose(v)
                return np.r_[100*(f[:3,3]-xyz[i]),.1*Rotation.from_matrix(desired.T@f[:3,:3]).as_rotvec(),.01*(v-prior)]
            solved=least_squares(residual,np.clip(q,*bounds),bounds=bounds,max_nfev=100,ftol=1e-7,xtol=1e-7,gtol=1e-7)
            q=solved.x;f=fk.pose(q)
            error=float(np.linalg.norm(f[:3,3]-xyz[i]));angle=float(np.linalg.norm(Rotation.from_matrix(desired.T@f[:3,:3]).as_rotvec()))
            if error>.002:
                message=f'{side} policy target unreachable within 2 mm ({error*1000:.2f} mm)'
                if not allow_prefix or len(output)<2:raise ValueError(message)
                failures.append({'side':side,'row':i,'position_error_m':error,'reason':message})
                break
            output.append(q.tolist()+[float(original[i,6])]);errors.append(error);angles.append(angle)
        result[key]=output
        accepted_metrics[side]=(errors,angles)
        report[side]={'max_position_error_m':max(errors,default=0.),'max_orientation_deviation_radians':max(angles,default=0.),'rows':count,'orientation_source':'policy_eef_rot6' if rotations is not None else 'legacy_sdk_joint_reference'}
    # Both arms execute the same time prefix. No later valid row can bypass
    # an earlier failed target on either active arm.
    executed=min(len(result[key]) for key in ('q_l_chunk','q_r_chunk'))
    maximum_displacement=0.
    for side,key in [('left','q_l_chunk'),('right','q_r_chunk')]:
        result[key]=result[key][:executed];report[side]['rows']=executed
        if side in active_sides:
            errors,angles=accepted_metrics[side]
            report[side]['max_position_error_m']=max(errors[:executed-1],default=0.)
            report[side]['max_orientation_deviation_radians']=max(angles[:executed-1],default=0.)
            start=fk.pose(state['q_'+side])[:3,3]
            maximum_displacement=max(maximum_displacement,*(float(np.linalg.norm(fk.pose(row[:6])[:3,3]-start)) for row in result[key]))
    report['_execution']={'requested_rows':requested_count,'executed_rows':executed,
                          'truncated':executed<requested_count,'rejected_targets':failures,
                          'max_tcp_displacement_m':maximum_displacement,
                          'requires_fresh_replan':executed<requested_count,
                          'position_tolerance_m':.002}
    return result,report
