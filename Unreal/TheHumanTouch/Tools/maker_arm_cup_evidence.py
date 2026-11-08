"""Empty-cup upright retention evidence; no liquid or grasp-force inference."""
import math


def update(evidence, timer, tilt_degrees, now, threshold=15.):
    if not all(math.isfinite(v) for v in (tilt_degrees, now, threshold)):
        raise ValueError('Cup orientation evidence must be finite')
    previous = timer.get('upright_sample')
    timer['upright_sample'] = now
    valid = (0 <= tilt_degrees <= threshold and evidence.get('height_above_start_cm', -1) >= 10
             and evidence.get('speed_cm_s', float('inf')) <= 10)
    if previous is not None and (now <= previous or now-previous > .25):
        timer.pop('upright_since', None)
    if valid:
        timer.setdefault('upright_since', now)
    else:
        timer.pop('upright_since', None)
    evidence.update(tilt_degrees=tilt_degrees, upright_threshold_deg=threshold,
                    current_slow_upright_above_10cm_seconds=now-timer.get('upright_since', now))
