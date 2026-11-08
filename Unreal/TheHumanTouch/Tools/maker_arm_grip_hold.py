"""Retain the last policy jaw-close setpoint while joints hold position."""
import math

def next_gap(current, desired, dt, max_speed=.04):
    if not all(math.isfinite(v) for v in (current, desired, dt, max_speed)):
        raise ValueError('Finite jaw state required')
    if not 0 <= current <= .104825 or not 0 <= desired <= .104825 or dt < 0 or not 0 < max_speed <= .2:
        raise ValueError('Jaw hold outside model limits')
    # Never reopen or invent a close command; .04m/s is below source pair limit .2m/s.
    return max(desired, current-min(dt, .05)*max_speed) if desired < current else current
