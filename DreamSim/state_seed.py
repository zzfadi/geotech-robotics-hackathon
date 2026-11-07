"""Recover a candidate pose; Unreal must verify it against every live link."""
import json

def recover_state(playback, observation, initial):
    for path, nested in ((playback, False), (observation, True), (initial, False)):
        try:
            value=json.loads(path.read_text())
            state=value['state'] if nested else value
            if all(key in state for key in ('q_left','q_right','gap_left_m','gap_right_m')):
                return state
        except (OSError, ValueError, KeyError, TypeError):
            continue
    raise ValueError('No candidate robot state available; no motion applied')
