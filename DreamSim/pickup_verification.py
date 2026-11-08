"""Conservative, target-specific lift evidence; never infers a grasp from height alone."""
import math
import time


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def verify_pickup(objective, target, status, requests=(), motion_chunks=(), now=None):
    result = {'target': target, 'measured_lift_pass': False, 'grasp_verified': False, 'designated_arm_support_verified': False,
              'outcome': 'unverified', 'reasons': []}
    if not isinstance(objective, dict):
        result['reasons'].append('No structured pickup objective')
        return result
    if (objective.get('kind') != 'lift_hold' or objective.get('target') != target
            or objective.get('side') not in ('left', 'right')):
        result['reasons'].append('Objective kind, target or arm is invalid')
        return result
    height, hold = objective.get('height_cm'), objective.get('hold_seconds')
    result.update(side=objective['side'], required_height_cm=height, required_hold_seconds=hold)
    # Runtime evidence currently accumulates a continuous hold above exactly 10 cm.
    # It cannot prove a different threshold, even if the current height is higher.
    if not finite_number(height) or height != 10 or not finite_number(hold) or hold <= 0:
        result['reasons'].append('Unsupported height or hold threshold')
        return result
    status = status or {}
    sampled = status.get('sampled_unix')
    now = time.time() if now is None else now
    if not finite_number(sampled) or not finite_number(now) or not 0 <= now-sampled <= 2:
        result['reasons'].append('Current physics evidence is missing or stale')
        return result
    if status.get('target') != target:
        result['reasons'].append('Runtime target does not match the commanded object')
        return result
    prop = status.get('props', {}).get(target)
    if not prop:
        result['reasons'].append('Commanded object has no current physics evidence')
        return result
    current = prop.get('height_above_start_cm')
    duration = prop.get('current_slow_above_10cm_seconds')
    speed = prop.get('speed_cm_s')
    result.update(height_cm=current, held_seconds=duration, speed_cm_s=speed,
                  physics_simulated=prop.get('physics_simulated'),
                  gravity_enabled=prop.get('gravity_enabled'), attached_to=prop.get('attached_to'))
    if not all(finite_number(x) for x in (current, duration, speed)):
        result['reasons'].append('Missing or nonfinite current measurement')
        return result
    api_returned = any(r.get('returned_action') is True and not r.get('error') for r in requests)
    moved = any(finite_number(m.get('max_joint_delta_rad')) and m['max_joint_delta_rad'] > 1e-5
                and not m.get('error') for m in motion_chunks)
    result.update(api_action_returned=api_returned, robot_motion_observed=moved)
    physical = (prop.get('physics_simulated') is True and prop.get('gravity_enabled') is True
                and prop.get('attached_to') is None)
    measured = current >= height and duration >= hold and speed <= 10 and physical and api_returned and moved
    result['measured_lift_pass'] = measured
    if not measured:
        result['outcome'] = 'failed'
        result['reasons'].append('Current lift, continuous hold, physics or API-driven motion criterion not met')
    else:
        # A shelf or collision ejection can satisfy these measurements. The present
        # runtime does not supply validated bilateral gripper support evidence.
        result['reasons'].append('Measured lift held; independent grasp review still required')
    return result
