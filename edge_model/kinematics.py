"""Pinned Maker Arm kinematics for the bench: repo FK plus a numpy-only IK.

FK is the repo's compiled CAD chain (DreamSim/compiled_fk.py on the pinned
URDF), so joint conventions match the Unreal tables and the dataset exporter.
IK is damped least squares with a numeric Jacobian, numpy only, so it runs on
a Jetson without SciPy. Frames: metres, right-handed, the left arm's mounted
base frame (x into the table workspace, z up).
"""
import math
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
for extra in (REPO / 'Unreal/TheHumanTouch/Tools', REPO / 'DreamSim'):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from compiled_fk import ArmFK  # noqa: E402  (repo module, pinned URDF)

GAP_MAX_M = 0.104825
# The fleet's cleared home pose (DreamSim/fleet_cups.json initial_state).
HOME = np.array([-8.3e-08, -1.3267562769368857, 1.5947274948757348,
                 -1.4680311465744582, 1.62e-08, -8.18e-08])
# Unreal station placement (yam_station.BASES), centimetres, left-handed UE.
BASES_UE_CM = {'left': (20.0, -40.0, 76.0), 'right': (20.0, 40.0, 76.0)}


def ue_to_left_base(location_cm):
    """Unreal table centimetres (left-handed) -> left arm base metres (RH)."""
    bx, by, bz = BASES_UE_CM['left']
    x, y, z = location_cm
    return np.array([(x - bx) / 100.0, -(y - by) / 100.0, (z - bz) / 100.0])


def rotvec(R):
    c = max(-1.0, min(1.0, (np.trace(R) - 1) / 2))
    a = math.acos(c)
    if a < 1e-9:
        return np.zeros(3)
    if math.pi - a < 1e-6:
        # Near pi: recover the axis from the symmetric part.
        w, v = np.linalg.eigh((R + R.T) / 2)
        return v[:, int(np.argmax(w))] * a
    return a / (2 * math.sin(a)) * np.array([R[2, 1] - R[1, 2], R[0, 2] - R[2, 0], R[1, 0] - R[0, 1]])


def approach_frame(direction_xy, pitch_deg=20.0):
    """Tool frame for a side grasp: z approaches horizontally, pitched down."""
    r = np.array([direction_xy[0], direction_xy[1], 0.0])
    r /= np.linalg.norm(r)
    a = math.radians(pitch_deg)
    z = r * math.cos(a) + np.array([0.0, 0.0, -1.0]) * math.sin(a)
    x = np.cross([0.0, 0.0, 1.0], z)
    x /= np.linalg.norm(x)
    return np.stack([x, np.cross(z, x), z], axis=1)


class Arm:
    def __init__(self):
        self.fk = ArmFK()
        self.low = self.fk.low + 1e-6
        self.high = self.fk.high - 1e-6

    def clip(self, q):
        return np.clip(np.asarray(q, dtype=float), self.low, self.high)

    def tcp(self, q):
        """(position, tool rotation) of grasp_center in the mounted base frame."""
        T = self.fk.pose(self.clip(q))
        return T[:3, 3].copy(), T[:3, :3] @ self.fk.tool_axes

    def ik(self, position, rotation, q0, weight=0.3, iterations=120, tol=1e-4):
        q = self.clip(q0)
        eye = np.eye(6)
        for _ in range(iterations):
            p, R = self.tcp(q)
            err = np.r_[position - p, weight * rotvec(rotation @ R.T)]
            if np.linalg.norm(err[:3]) < tol and np.linalg.norm(err[3:]) < 1e-3 * weight:
                break
            J = np.zeros((6, 6))
            for j in range(6):
                step = 1e-5 if q[j] + 1e-5 <= self.high[j] else -1e-5
                dq = np.zeros(6)
                dq[j] = step
                p2, R2 = self.tcp(q + dq)
                J[:, j] = np.r_[p2 - p, weight * rotvec(R2 @ R.T)] / step
            delta = J.T @ np.linalg.solve(J @ J.T + 4e-4 * eye, err)
            q = self.clip(q + np.clip(delta, -0.2, 0.2))
        p, R = self.tcp(q)
        return q, float(np.linalg.norm(position - p)), float(np.linalg.norm(rotvec(rotation @ R.T)))
