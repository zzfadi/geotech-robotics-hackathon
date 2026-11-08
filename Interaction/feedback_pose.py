"""Measured SDK feedback to a visual URDF pose; no targets, jaw inference or output."""
import math
import numpy as np
from .protocol import vector
from .control import model


def measured_feedback_pose(state, calibration, now):
    """Convert only fresh measured joints using an explicitly registered calibration.

    No deadman is needed for read-only visualization. A visual pose does not arm
    hardware. Unknown gripper calibration remains unknown, never a zero gap.
    """
    if not isinstance(calibration, dict) or calibration.get('validated') is not True or not calibration.get('validation_evidence'):
        raise ValueError('Validated hardware calibration is unavailable')
    if state.get('side') not in ('left', 'right') or state['side'] != calibration.get('side'):
        raise ValueError('Calibrated arm side mismatch')
    if not calibration.get('id') or not calibration.get('serial') or state.get('calibration_id') != calibration['id'] or state.get('serial') != calibration['serial']:
        raise ValueError('Calibration/adapter identity mismatch')
    stamp = state.get('feedback_unix')
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or not math.isfinite(now) or not 0 <= now-stamp <= .25:
        raise ValueError('Stale or future hardware feedback')
    q = np.array(vector(state.get('q_sdk_rad'), 6, 'measured q_sdk_rad'))
    matrix = np.asarray(calibration.get('sdk_to_urdf_matrix'), dtype=float)
    offset = np.array(vector(calibration.get('urdf_offset_rad'), 6, 'offset'))
    if matrix.shape != (6, 6) or not np.isfinite(matrix).all() or np.linalg.cond(matrix) > 100:
        raise ValueError('Invalid calibrated motor mapping')
    measured = matrix @ q + offset
    joints = [j for j in model().findall('joint') if j.get('type') == 'revolute']
    bounds = np.array([[float(j.find('limit').get(k)) for k in ('lower', 'upper')] for j in joints])
    if len(joints) != 6 or np.any(measured < bounds[:, 0]) or np.any(measured > bounds[:, 1]):
        raise ValueError('Calibrated measured pose outside official URDF limits')
    return {'table': 10, 'side': state['side'], 'serial': state['serial'],
            'calibration_id': calibration['id'], 'feedback_unix': stamp,
            'q_urdf_rad': measured.tolist(), 'joint_names': [j.get('name') for j in joints],
            'jaw_gap_m': None, 'pose_source': 'measured_sdk_feedback',
            'physical_output_enabled': False}
