"""Precompiled six-joint FK from the pinned URDF; no changed axes, limits or TCP."""
import math
import numpy as np
from maker_arm_model import model
from yam_kinematics import origin, rotation, mul


class ArmFK:
    def __init__(self):
        root = model()
        revolute = root.findall("joint[@type='revolute']")
        self.names = [j.get('name') for j in revolute]
        self.low = np.array([float(j.find('limit').get('lower')) for j in revolute])
        self.high = np.array([float(j.find('limit').get('upper')) for j in revolute])
        by_child = {j.find('child').get('link'): j for j in root.findall('joint')}
        chain, link = [], 'grasp_center'
        while link in by_child:
            joint = by_child[link]
            chain.append(joint)
            link = joint.find('parent').get('link')
        if link != 'base_link' or len(revolute) != 6:
            raise ValueError('Unexpected Maker Arm chain')
        self.chain = []
        for joint in reversed(chain):
            transform = np.asarray(origin(joint.find('origin')), dtype=float)
            index, skew, outer = None, None, None
            if joint.get('type') == 'revolute':
                index = self.names.index(joint.get('name'))
                axis = np.fromstring(joint.find('axis').get('xyz'), sep=' ')
                axis /= np.linalg.norm(axis)
                x, y, z = axis
                skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
                outer = np.outer(axis, axis)
            elif joint.get('type') != 'fixed':
                raise ValueError('Unexpected moving joint before grasp frame')
            self.chain.append((transform, index, skew, outer))
        self.mount = np.array(mul(rotation([0, 0, 1], math.pi), rotation([1, 0, 0], math.pi/2)))
        # Dream custom-arm convention: TCP z=approach, x=jaw, y=z cross x.
        # grasp_center orientation is the flange orientation in this pinned URDF.
        grasp = root.find("joint[@name='grasp_center_joint']/origin")
        if grasp.get('rpy', '0 0 0') != '0 0 0':
            raise ValueError('Review changed grasp axes')
        z = np.array([0., 0., -1.])
        x = np.fromstring(root.find("joint[@name='gripper_left_joint']/axis").get('xyz'), sep=' ')
        x -= np.dot(x, z)*z
        x /= np.linalg.norm(x)
        self.tool_axes = np.stack((x, np.cross(z, x), z), axis=1)

    def pose(self, q):
        q = np.asarray(q, dtype=float)
        if q.shape != (6,) or not np.isfinite(q).all() or np.any(q < self.low) or np.any(q > self.high):
            raise ValueError('Joint command outside exact model limits')
        transform = self.mount.copy()
        for fixed, index, skew, outer in self.chain:
            transform = transform @ fixed
            if index is not None:
                c, s = math.cos(q[index]), math.sin(q[index])
                r = c*np.eye(3) + (1-c)*outer + s*skew
                transform[:, :3] = transform[:, :3] @ r
        return transform


def decode_rot6(value):
    """SDK dream_ik.decode.rot6d_to_mat convention: Gram-Schmidt columns."""
    value = np.asarray(value, dtype=float)
    if value.shape != (6,) or not np.isfinite(value).all():
        raise ValueError('Invalid policy EEF rotation')
    first, second = value[:3].copy(), value[3:].copy()
    if np.linalg.norm(first) < 1e-8:
        raise ValueError('Degenerate policy EEF rotation')
    first /= np.linalg.norm(first)
    second -= np.dot(first, second)*first
    if np.linalg.norm(second) < 1e-8:
        raise ValueError('Degenerate policy EEF rotation')
    second /= np.linalg.norm(second)
    return np.stack((first, second, np.cross(first, second)), axis=1)
