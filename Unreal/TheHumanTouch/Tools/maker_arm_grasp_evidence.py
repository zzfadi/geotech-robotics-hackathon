"""Conservative contact-evidence accumulator; never declares pickup success.

The runtime adapter must supply measured geometry and a complete contact snapshot.
Closure-limit labels or AABB overlap must never populate contact_pair_left/right.
Missing/partial contact information is unknown, never evidence of no supports.
"""
import math


def update(state, sample, *, hold_seconds=2.0, max_sample_gap=.25):
    now = sample['monotonic']
    if not isinstance(now, (int, float)) or not math.isfinite(now):
        raise ValueError('Finite monotonic timestamp required')
    previous = state.get('last_sample')
    if previous is not None and now <= previous:
        raise ValueError('Samples must have increasing monotonic timestamps')
    state['last_sample'] = now

    def below(key, value):
        v = sample.get(key)
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v <= value

    def above(key, value):
        v = sample.get(key)
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= value

    checks = {
        'free_physics': sample.get('physics_simulated') is True and sample.get('gravity_enabled') is True
                        and sample.get('attachment_checked') is True and sample.get('attached_to') is None,
        'lift_height': above('height_above_start_cm', 10),
        'slow': below('speed_cm_s', 10),
        'clear_of_table': above('lowest_collision_bound_above_table_cm', 1),
        'bilateral_geometry': below('left_pad_surface_distance_cm', .2) and below('right_pad_surface_distance_cm', .2)
                              and sample.get('inside_finite_pad_faces') is True,
        'relative_retention': below('target_tcp_relative_drift_cm', .5),
        'actual_bilateral_contact': sample.get('contact_snapshot_complete') is True
                                    and sample.get('contact_pair_left') is True and sample.get('contact_pair_right') is True,
        'no_other_support': sample.get('contact_snapshot_complete') is True and sample.get('other_contact_bodies') == [],
    }
    geometry_valid = (all(v for k, v in checks.items() if k not in ('actual_bilateral_contact', 'no_other_support'))
                      and not sample.get('other_contact_bodies'))
    valid = all(checks.values())
    if previous is not None and now-previous > max_sample_gap:
        state.pop('candidate_since', None)
        state.pop('review_since', None)
    if geometry_valid:
        state.setdefault('review_since', now)
    else:
        state.pop('review_since', None)
    review_duration = now-state.get('review_since', now)
    if valid:
        state.setdefault('candidate_since', now)
    else:
        state.pop('candidate_since', None)
    duration = now-state.get('candidate_since', now)
    state.update(checks=checks, candidate_hold_seconds=duration,
                 geometry_review_hold_seconds=review_duration,
                 geometry_review_candidate=geometry_valid and review_duration >= hold_seconds,
                 physical_grasp_candidate=valid and duration >= hold_seconds,
                 grasp_verified=False, independent_video_review_required=True)
    return state
