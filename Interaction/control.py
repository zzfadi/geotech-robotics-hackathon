"""Deterministic virtual work and bounded position-admittance proposals, never motor commands."""
import math
import sys
from pathlib import Path
import numpy as np
from .protocol import validate_contact, vector
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'Unreal/TheHumanTouch/Tools'))
from maker_arm_model import model, frames
from yam_kinematics import origin

EFFORT_NM=np.array([1.5,5.,6.,2.5,1.,1.])
RATE_RAD_S=math.radians(3)
LEAD_RAD=math.radians(2)

def ue_contact_to_urdf(point_cm, force_n, world_from_urdf):
    """Convert polar force and point using a registered simulation transform.

    Matrix maps URDF RH metres to UE LH metres (including calibrated origin).
    Torque is derived AFTER conversion, avoiding pseudovector reflection errors.
    This transform never establishes the separate hardware motor calibration.
    """
    matrix=np.asarray(world_from_urdf,dtype=float)
    if matrix.shape!=(4,4) or not np.isfinite(matrix).all() or not np.allclose(matrix[3],[0,0,0,1]):
        raise ValueError('Invalid registered coordinate transform')
    rotation=matrix[:3,:3]
    if not np.allclose(rotation.T@rotation,np.eye(3),atol=1e-7) or not np.isclose(np.linalg.det(rotation),-1,atol=1e-7):
        raise ValueError('Expected orthonormal RH-to-LH transform, without scale')
    point=np.array(vector(point_cm,3,'point_cm'))*.01
    force=np.array(vector(force_n,3,'force_n'))
    return (rotation.T@(point-matrix[:3,3])).tolist(),(rotation.T@force).tolist()


def point_jacobian(q_urdf,link,point_base_m):
    q=np.array(vector(q_urdf,6,'q_urdf'));point=np.array(vector(point_base_m,3,'point_m'))
    root=model();poses={k:np.array(v) for k,v in frames(q.tolist(),0.).items()}
    if link not in poses or link.startswith('gripper_'):raise ValueError('Unsupported contact link')
    joints=root.findall('joint');parent={j.find('child').get('link'):j for j in joints}
    chain=set();at=link
    while at in parent:
        j=parent[at];chain.add(j.get('name'));at=j.find('parent').get('link')
    jac=np.zeros((3,6));column=0
    for j in joints:
        if j.get('type')!='revolute':continue
        if j.get('name') in chain:
            t=poses[j.find('parent').get('link')]@np.array(origin(j.find('origin')))
            axis=np.array([float(x)for x in j.find('axis').get('xyz').split()]);axis/=np.linalg.norm(axis)
            jac[:,column]=np.cross(t[:3,:3]@axis,point-t[:3,3])
        column+=1
    return jac

def contact_torque(q_urdf,link,point_base_m,force_n):
    return point_jacobian(q_urdf,link,point_base_m).T@np.array(vector(force_n,3,'force_n'))

def bounded_admittance(q_sdk,dq_sdk,tau_sdk,previous_target,dt,limits,measured_torque):
    q=np.array(vector(q_sdk,6,'q_sdk'));dq=np.array(vector(dq_sdk,6,'dq_sdk'))
    tau=np.array(vector(tau_sdk,6,'tau_sdk'));previous=np.array(vector(previous_target,6,'previous_target'))
    measured=np.array(vector(measured_torque,6,'measured_torque'))
    bounds=np.asarray(limits,dtype=float)
    if bounds.shape!=(6,2) or not np.isfinite(bounds).all() or np.any(bounds[:,0]>=bounds[:,1]):raise ValueError('Invalid SDK limits')
    if not math.isfinite(dt) or not 0<dt<=.04:raise ValueError('Expected bounded25Hz control step')
    if np.any(q<bounds[:,0]) or np.any(q>bounds[:,1]):raise ValueError('Measured SDK position outside nominal limits')
    if np.any(abs(dq)>math.radians(20)) or np.any(abs(measured)>EFFORT_NM):raise ValueError('Measured speed/effort guard')
    if np.any(abs(previous-q)>LEAD_RAD+1e-9):raise ValueError('Previous demand exceeds2degree lead')
    # This is a virtual contribution, not a verified motor effort command.
    virtual=np.clip(tau,-EFFORT_NM-measured,EFFORT_NM-measured)
    velocity=np.clip(.01*virtual-.1*dq,-RATE_RAD_S,RATE_RAD_S)
    low=np.maximum.reduce([bounds[:,0],q-LEAD_RAD,previous-RATE_RAD_S*dt])
    high=np.minimum.reduce([bounds[:,1],q+LEAD_RAD,previous+RATE_RAD_S*dt])
    if np.any(low>high):raise ValueError('No rate/lead/limit-safe target')
    target=np.clip(previous+velocity*dt,low,high)
    return {'virtual_torque_nm':virtual.tolist(),'target_sdk_rad':target.tolist(),'velocity_demand_rad_s':velocity.tolist()}

def calibrated_plan(contact,state,calibration,now,previous_target,dt=.04):
    if not isinstance(calibration,dict) or calibration.get('validated') is not True or not calibration.get('validation_evidence'):
        raise ValueError('Validated hardware calibration is unavailable')
    if state.get('side') not in ('left','right') or state.get('side') != calibration.get('side'):
        raise ValueError('Calibrated arm side mismatch')
    if state.get('calibration_id')!=calibration.get('id') or state.get('serial')!=calibration.get('serial'):
        raise ValueError('Calibration/adapter identity mismatch')
    if state.get('local_deadman') is not True:raise ValueError('Local deadman not held')
    age=now-state.get('feedback_unix',float('-inf'))
    if not math.isfinite(age) or not 0<=age<=.25:raise ValueError('Stale hardware feedback')
    faults=state.get('faults')
    if not isinstance(faults,list) or len(faults)!=6 or any(type(v) not in (bool,int) for v in faults):
        raise ValueError('Six explicit motor fault states required')
    if any(faults) or any(t>=42 for t in vector(state.get('temperature_c'),6,'temperature_c')):
        raise ValueError('Hardware fault/temperature guard')
    q=np.array(vector(state.get('q_sdk_rad'),6,'q_sdk_rad'))
    mapping=np.asarray(calibration.get('sdk_to_urdf_matrix'),dtype=float);offset=np.array(vector(calibration.get('urdf_offset_rad'),6,'offset'))
    if mapping.shape!=(6,6) or not np.isfinite(mapping).all() or np.linalg.cond(mapping)>100:raise ValueError('Invalid calibrated motor mapping')
    q_urdf=mapping@q+offset
    if contact is None:
        # Loss of contact does not command a return or release an elevated arm.
        return {'virtual_torque_nm':[0.]*6,'target_sdk_rad':None,'action':'retain_guarded_hold','physical_output_enabled':False}
    event=validate_contact(contact,now)
    if event['side'] != state['side']:raise ValueError('Contact belongs to another arm')
    tau_urdf=contact_torque(q_urdf.tolist(),event['link'],event['point_m'],event['force_n'])
    result=bounded_admittance(q.tolist(),state['dq_sdk_rad_s'],(mapping.T@tau_urdf).tolist(),previous_target,dt,state['limits_sdk_rad'],state['torque_nm'])
    return {**result,'action':'preview_position_admittance','physical_output_enabled':False,'calibration_id':calibration['id']}
