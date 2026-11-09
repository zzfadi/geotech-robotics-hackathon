"""One Dream call from the live station. Prints and saves Dream's action chunk; never actuates anything.

Frames: the three arm cameras (overhead, wrist_l, wrist_r) from the Jetson dashboard (vision/live_view.py), through
the Mac's SSH tunnel. State: both arms' joints and jaw gaps, which Dream requires. Give measured SDK readings in a
JSON file (--state-sdk), or --nominal for the folded zero pose (labelled NOMINAL in the report: not measured).
Embodiment, IK model and checkpoint come from probe.configure / model.prepare, the same as the Table 10 runner.

    # key once, in ~/.dreamlabs/credentials as DREAM_API_KEY=...
    ~/hub-mac/.venv/bin/python DreamSim/dream_live.py --nominal
    ~/hub-mac/.venv/bin/python DreamSim/dream_live.py --state-sdk state.json --prompt "..."

state.json (SDK degrees as `arm scan` prints them, gripper last; whole-turn wraps are folded back):
    {"right": [j1, j2, j3, j4, j5, j6, gripper], "left": [j1, j2, j3, j4, j5, j6, gripper]}
"""
import argparse
import base64
import io
import json
import math
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))

JAW = .104825                    # model jaw gap range, metres
SIGN = (1, 1, 1, 1, -1, 1)       # SDK motor -> URDF: wrist yaw is inverted (maker_arm_real_mapping.py)
GRIP_OPEN_DEG = -96.0            # SDK gripper: 0 closed, -96 open; linear gap map (unmeasured linkage)
SALEH_PROMPT = ("Use the right arm to pick up the water bottle, hand it to me, release it, and return home. "
                "Keep the left arm still.")
ROLES = ("overhead", "wrist_l", "wrist_r")


def unwrap(deg):
    return (deg + 180.0) % 360.0 - 180.0


def sdk_to_obs(sdk7):
    """Seven SDK degrees -> (six URDF radians, jaw gap metres)."""
    if len(sdk7) != 7:
        raise ValueError('need 7 SDK values per arm: six joints then the gripper')
    q = [s * math.radians(unwrap(v)) for s, v in zip(SIGN, sdk7[:6])]
    gap = JAW * min(1.0, max(0.0, unwrap(sdk7[6]) / GRIP_OPEN_DEG))
    return q, gap


def obs_to_sdk(row):
    """One action row (six URDF radians + normalized grip) -> seven SDK degrees, for reading the plan."""
    joints = [round(s * math.degrees(v), 1) for s, v in zip(SIGN, row[:6])]
    return joints + [round(float(row[6]) * GRIP_OPEN_DEG, 1)]


def fetch_frames(base, out):
    frames = {}
    for role in ROLES:
        with urllib.request.urlopen(f'{base}/cam/{role}.jpg', timeout=5) as r:
            raw = r.read()
        (out / f'{role}.jpg').write_bytes(raw)
        buf = io.BytesIO()
        Image.open(io.BytesIO(raw)).convert('RGB').resize((320, 176), Image.Resampling.LANCZOS).save(buf, format='JPEG', quality=90)
        frames['video.' + role] = base64.b64encode(buf.getvalue()).decode('ascii')
    return frames


def summarize(action):
    out = {}
    for side, key in (('left', 'q_l_chunk'), ('right', 'q_r_chunk')):
        chunk = np.asarray(action.get(key, []), dtype=float)
        if not chunk.size:
            continue
        sdk = [obs_to_sdk(r) for r in chunk]
        travel = np.abs(np.asarray(sdk)[-1, :6] - np.asarray(sdk)[0, :6])
        out[side] = {'steps': len(chunk), 'first_sdk_deg': sdk[0], 'last_sdk_deg': sdk[-1],
                     'max_joint_travel_deg': round(float(travel.max()), 1),
                     'grip_open_fraction': [round(float(chunk[0, 6]), 2), round(float(chunk[-1, 6]), 2)]}
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--prompt', default=SALEH_PROMPT)
    p.add_argument('--cams', default='http://127.0.0.1:8790', help='Jetson dashboard URL (Mac end of the tunnel)')
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument('--state-sdk', type=Path, help='measured SDK degrees for both arms (see above)')
    g.add_argument('--nominal', action='store_true', help='folded zero pose with open jaws; NOT measured')
    p.add_argument('--checkpoint', default='dreamlabs/dream-1-beta', help='the Table 10 runner uses dream-1-beta')
    p.add_argument('--out', type=Path, default=Path('~/hub-mac/dream-runs').expanduser())
    a = p.parse_args()

    if a.nominal:
        sdk = {'right': [0, 0, 0, 0, 0, 0, GRIP_OPEN_DEG], 'left': [0, 0, 0, 0, 0, 0, GRIP_OPEN_DEG]}
        state_source = 'NOMINAL folded zero pose, jaws open (not measured)'
    else:
        sdk = json.loads(a.state_sdk.read_text())
        state_source = f'measured SDK readings from {a.state_sdk}'
    obs = {'prompt': a.prompt}
    for side in ('left', 'right'):
        q, gap = sdk_to_obs(sdk[side])
        obs[f'state.{side}_arm'] = np.asarray(q)
        obs[f'state.{side}_grip'] = [gap / JAW]

    run = a.out / time.strftime('%Y%m%d-%H%M%S')
    run.mkdir(parents=True, exist_ok=True)
    frames = fetch_frames(a.cams, run)
    print(f'frames saved in {run}', flush=True)

    from dream import DreamClient
    from model import prepare
    from probe import configure
    arm = prepare(run / 'model', tcp_back=0.)
    client = DreamClient()
    try:
        configure(client, arm, a.checkpoint)
        print('policy configured; warming IK', flush=True)
        if not client.wait_for_ik(240):
            raise TimeoutError('Dream IK warmup exceeded 240 s')
        client.set_observation(frames, obs)
        started = time.monotonic()
        action = client.get_action()
        elapsed = time.monotonic() - started
    finally:
        client.close()

    report = {'scope': 'inference only; nothing actuated', 'prompt': a.prompt, 'checkpoint': a.checkpoint,
              'state_source': state_source, 'state_sdk_deg': sdk, 'get_action_seconds': round(elapsed, 3),
              'summary': summarize(action), 'action': action}
    (run / 'report.json').write_text(json.dumps(report, indent=2, default=lambda x: x.tolist() if hasattr(x, 'tolist') else str(x)) + '\n')
    print(json.dumps({k: report[k] for k in ('get_action_seconds', 'state_source', 'summary')}, indent=2), flush=True)
    print(f'report: {run / "report.json"}')


if __name__ == '__main__':
    main()
