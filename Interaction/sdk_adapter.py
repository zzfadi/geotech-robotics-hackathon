"""Read an ALREADY connected pinned Maker Arm SDK object. Never connects/enables/moves/releases."""
import math

def sample_connected_arm(arm,now,*,serial,side,calibration_id=None,local_deadman=False):
    # Official JointConfig: motor = joint*direction + offset. Direction is±1,
    # so SDK joint velocity/torque use that same sign; raw values remain logged.
    # This does NOT establish the missing SDK-to-physical-URDF calibration.
    if side not in ('left','right'):raise ValueError('Explicit calibrated arm side required')
    if not math.isfinite(now):raise ValueError('Finite host timestamp required')
    joints=list(arm.config.joints);motors=list(arm.motors)
    if len(joints)<6 or len(motors)<6 or [j.motor_id for j in joints[:6]]!=list(range(1,7)):
        raise ValueError('Expected Maker Arm motor IDs1..6; gripper excluded')
    q=list(arm.get_joint_positions())[:6]
    feedback=[m.feedback for m in motors[:6]]
    ages=[m.feedback_age for m in motors[:6]]
    if any(f is None for f in feedback) or not all(math.isfinite(a) and 0<=a<=.25 for a in ages):
        raise ValueError('Missing/stale motor feedback')
    fields=[[getattr(f,key) for f in feedback] for key in ('velocity','torque','temperature')]
    if not all(math.isfinite(v) for row in [q,*fields] for v in row):raise ValueError('Nonfinite motor feedback')
    directions=[j.direction for j in joints[:6]]
    if any(d not in (-1,1) for d in directions):raise ValueError('Invalid SDK joint direction')
    return {'serial':serial,'side':side,'calibration_id':calibration_id,'local_deadman':bool(local_deadman),
            'feedback_unix':now-max(ages),'feedback_age_s':ages,'q_sdk_rad':q,
            'dq_sdk_rad_s':[d*v for d,v in zip(directions,fields[0])],
            'torque_nm':[d*v for d,v in zip(directions,fields[1])],
            'raw_motor_velocity_rad_s':fields[0],'raw_motor_torque_nm':fields[1],
            'temperature_c':fields[2],
            'faults':[bool(m.fault_latched or f.warning_flag or f.fault_flag) for m,f in zip(motors[:6],feedback)],
            'limits_sdk_rad':[[j.lo,j.hi] for j in joints[:6]],'sdk_state':arm.state.name,
            'physical_output_enabled':False,'mode_telemetry_verified':False,
            'status':'read_only; physical URDF registration and local enable remain unavailable'}
