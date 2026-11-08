"""Bound repeated partial policy execution using measured playback, not task success."""
import math


class PrefixGuard:
    def __init__(self, max_consecutive=6, max_stalled=3):
        self.consecutive = 0
        self.stalled = 0
        self.max_consecutive = max_consecutive
        self.max_stalled = max_stalled

    def observe(self, execution, joint_delta_rad, gap_delta_m):
        if not execution or not execution.get('truncated'):
            self.consecutive = self.stalled = 0
            return None
        if any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
               for v in (joint_delta_rad, gap_delta_m)):
            raise ValueError('Finite measured playback progress required')
        self.consecutive += 1
        self.stalled = self.stalled+1 if joint_delta_rad < 1e-4 and gap_delta_m < 1e-5 else 0
        if self.stalled >= self.max_stalled:
            return 'three consecutive feasible prefixes produced no measurable joint or gripper progress'
        if self.consecutive >= self.max_consecutive:
            return 'six consecutive truncated policy horizons; bounded replanning stopped'
        return None
