"""Scripted expert for the reach-and-grasp: state-based, so it can label any state.

It plans from the arm's measured joints and a cup *estimate* (what the overhead
camera would report), never from ground truth, so its own misses are honest.
Phases: travel to a pregrasp 10 cm back along a 20-degree pitched side approach,
slide in, close, lift 12.5 cm at 4 cm/s, then hold. A closed gripper with no cup
reopens and backs off (at most two retries).
"""
import numpy as np

from edge_model.bench import CUP_HEIGHT, DT, GAP_MAX_M
from edge_model.kinematics import approach_frame

TRAVEL = 0.15 * DT      # m per step between waypoints
APPROACH = 0.06 * DT    # m per step on the final approach
LIFT = 0.04 * DT
TRAVEL_JOINT = 0.6 * DT  # rad per step, joint space        # m per step while lifting (4 cm/s, under the 10 cm/s gate)
LIFT_HEIGHT = 0.125
BACKOFF = 0.10


class Expert:
    name = 'expert'

    def __init__(self, cup_estimate, max_retries=2):
        est = np.asarray(cup_estimate, dtype=float)
        self.grasp = np.array([est[0], est[1], CUP_HEIGHT / 2])
        self.R = approach_frame(est[:2])
        self.pre = self.grasp - BACKOFF * self.R[:, 2]
        self.retries = 0
        self.max_retries = max_retries
        self.lift_top = None
        self.lift_R = None
        self.retreating = False
        self.q_pre = None
        self.reachable = True

    def _pregrasp_joints(self, arm, q):
        best = None
        for seed in (q, arm.clip(q + [0.3, 0, 0, 0, 0, 0]), arm.clip(q - [0.3, 0, 0, 0, 0, 0])):
            sol, err, ang = arm.ik(self.pre, self.R, seed, iterations=200)
            if best is None or err < best[1]:
                best = (sol, err)
        self.reachable = best[1] < 0.004
        return best[0]

    def act(self, table):
        """(absolute joint target, grip target 0..1, phase name)."""
        arm, q = table.arm, table.q
        if self.q_pre is None:
            self.q_pre = self._pregrasp_joints(arm, q)
        p, R_now = arm.tcp(q)
        held = table.held()
        if held is not None:
            if self.lift_top is None:
                self.lift_top = p + np.array([0, 0, LIFT_HEIGHT])
                self.lift_R = R_now
            target = p.copy()
            target[:2] = self.lift_top[:2]
            target[2] = min(self.lift_top[2], p[2] + LIFT)
            phase = 'lift' if self.lift_top[2] - p[2] > 0.003 else 'hold'
            return self._solve(arm, q, target, self.lift_R), 0.0, phase
        self.lift_top = None
        partly_closed = table.gap < 0.95 * GAP_MAX_M
        missed = table.gap < 0.045
        if missed and not self.retreating:
            # Closed on nothing: reopen, back off, retry while retries remain.
            if self.retries >= self.max_retries:
                return q.copy(), 0.0, 'give up'
            self.retries += 1
            self.retreating = True
        if self.retreating:
            target = self._toward(p, self.pre, APPROACH * 2)
            if np.linalg.norm(p - self.pre) < 0.01 and not partly_closed:
                self.retreating = False
            return self._solve(arm, q, target, self.R), 1.0, 'back off'
        if partly_closed:
            if np.linalg.norm(p - self.grasp) <= 0.006:
                return q.copy(), 0.0, 'close'
            return q.copy(), 1.0, 'open'
        # Distance from the pre->grasp segment decides travel versus approach.
        seg = self.grasp - self.pre
        s = float(np.clip(np.dot(p - self.pre, seg) / np.dot(seg, seg), 0, 1))
        off_line = np.linalg.norm(p - (self.pre + s * seg))
        if off_line > 0.012:
            if not self.reachable:
                return q.copy(), 1.0, 'unreachable'
            # Joint-space travel to the precomputed pregrasp: smooth and table-safe.
            return self._toward(q, self.q_pre, TRAVEL_JOINT), 1.0, 'travel'
        if np.linalg.norm(p - self.grasp) > 0.004:
            target = self._toward(p, self.grasp, APPROACH)
            return self._solve(arm, q, target, self.R), 1.0, 'approach'
        return q.copy(), 0.0, 'close'

    @staticmethod
    def _toward(p, goal, step):
        d = goal - p
        n = np.linalg.norm(d)
        return goal.copy() if n <= step else p + d * (step / n)

    @staticmethod
    def _solve(arm, q, target, R):
        sol, _, _ = arm.ik(target, R, q, iterations=40)
        return sol
