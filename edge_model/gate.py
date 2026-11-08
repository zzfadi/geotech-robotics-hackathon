"""Positive-episode gate, same thresholds as DreamSim/demonstration_dataset.py.

measured_pass: a continuous 5 s window where the TARGET cup is held by both
jaws, its lowest point is at least 10 cm above the table, it tilts at most 15
degrees, moves at most 10 cm/s, is not attached and touches nothing else.

dream_export_eligible is stricter and is never granted here: it also needs
real simulated physics and a named human review (positive_eligibility in the
exporter). Bench episodes report physics_simulated=False, so they can train
the small model but can never be exported to Dream as positive demonstrations.
"""
import math

CLEARANCE_CM = 10.0
HOLD_S = 5.0
MAX_TILT = 15.0
MAX_SPEED = 10.0

CHECKS = (
    ('held by both jaws', lambda e: e.get('bilateral_jaw_contacts') is True),
    ('touching something else', lambda e: e.get('support_snapshot_complete') is True and e.get('other_support_contacts') == []),
    ('attached instead of held', lambda e: 'attached_to' in e and e['attached_to'] is None),
    ('gravity off', lambda e: e.get('gravity_enabled') is True),
    ('below 10 cm clearance', lambda e: _num(e.get('lowest_point_clearance_cm')) and e['lowest_point_clearance_cm'] >= CLEARANCE_CM),
    ('tilted over 15 degrees', lambda e: _num(e.get('tilt_degrees')) and 0 <= e['tilt_degrees'] <= MAX_TILT),
    ('moving over 10 cm/s', lambda e: _num(e.get('speed_cm_s')) and 0 <= e['speed_cm_s'] <= MAX_SPEED),
)


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def evaluate(times, evidence, target_held_ok=True):
    """Score one episode. times: seconds; evidence: per-sample target-cup dicts."""
    since, best, first_pass = None, 0.0, None
    blocking = {}
    for t, e in zip(times, evidence):
        failed = [name for name, check in CHECKS if not check(e)]
        if failed:
            since = None
            blocking[failed[0]] = blocking.get(failed[0], 0) + 1
            continue
        if since is None:
            since = t
        best = max(best, t - since)
        if first_pass is None and t - since >= HOLD_S - 1e-9:
            first_pass = t
    passed = first_pass is not None and target_held_ok
    reasons = []
    if not target_held_ok:
        reasons.append('wrong cup')
    if not passed and blocking:
        # The criterion that blocked the longest is the honest headline.
        reasons.append(max(blocking.items(), key=lambda kv: kv[1])[0])
    physics = all(e.get('physics_simulated') is True for e in evidence) if evidence else False
    return {
        'measured_pass': passed,
        'longest_hold_s': round(best, 2),
        'passed_at_s': first_pass,
        'max_clearance_cm': round(max((e.get('lowest_point_clearance_cm') or 0) for e in evidence), 2) if evidence else 0.0,
        'reasons': reasons,
        'physics_simulated': physics,
        'dream_export_eligible': False,
        'dream_export_note': 'Needs Isaac/Chaos physics and a named review (DreamSim/demonstration_dataset.py)',
    }
