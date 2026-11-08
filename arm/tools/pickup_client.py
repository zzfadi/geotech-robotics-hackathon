"""Client for pickup_server.py with level-gripper inverse kinematics.

Kinematics use the SDK gravity model's raw-motor chain plus the URDF grasp_center
(0.1696 m along link_007 -z). Frame: x forward, y left/right, z up, metres, from
the arm base. Provisional: not a calibrated hardware model.

  pickup_client.py status
  pickup_client.py fk Q1..Q6
  pickup_client.py joints Q1..Q6 [--speed 5]
  pickup_client.py tip X Y Z [--pitch 0] [--speed 5] [--dry]
  pickup_client.py nudge DX DY DZ [--pitch 0] [--speed 3] [--dry]
  pickup_client.py grip DEG [--wait 4]
  pickup_client.py clear
  pickup_client.py rest
"""
import argparse
import json
import math
import socket
import sys

import numpy as np
from scipy.optimize import least_squares
from maker_arm import gravity as g

GRASP = np.array([0., 0., -0.1696])
LIMITS = [(-158.2, 156.1), (-175.3, -3.2), (2.8, 236.1), (-65.2, 104.0), (-97.6, 78.0), (-153.2, 152.0)]


def fk(q):
    rot, o = np.array(g._ROOT), np.zeros(3)
    for (xyz, fixed, axis), qi in zip(g._FIXED, q):
        o = o + rot @ np.array(xyz)
        rot = rot @ np.array(fixed) @ np.array(g._about_z(axis[2] * math.radians(qi)))
    return o + rot @ GRASP, rot


def approach(q):
    return -fk(q)[1][:, 2]


def ik(tip, seed, pitch_deg=0.):
    """Solve joints 1-4 for the tip position and approach pitch; joints 5-6 keep the seed."""
    tip = np.asarray(tip, float)
    seed = list(seed)

    def full(x):
        return list(x) + seed[4:6]

    def res(x):
        q = full(x)
        p, rot = fk(q)
        a = -rot[:, 2]
        pitch = math.degrees(math.asin(max(-1, min(1, a[2]))))
        return np.concatenate([(p - tip) * 1000, [(pitch - pitch_deg) * 2],
                               (np.array(x) - np.array(seed[:4])) * 0.01])

    lo = [l + 1 for l, _ in LIMITS[:4]]
    hi = [h - 1 for _, h in LIMITS[:4]]
    x0 = np.clip(seed[:4], lo, hi)
    r = least_squares(res, x0, bounds=(lo, hi))
    q = full(r.x)
    p, _ = fk(q)
    err_mm = float(np.linalg.norm(p - tip) * 1000)
    return [float(v) for v in q], err_mm


PORT = 8765


def send(cmd, port=None):
    port = port or PORT
    with socket.create_connection(('127.0.0.1', port), timeout=180) as s:
        s.sendall((json.dumps(cmd) + '\n').encode())
        buf = b''
        while not buf.endswith(b'\n'):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf)


def show(reply):
    keep = {k: v for k, v in reply.items() if k not in ('cmd_deg', 'age_s')}
    if 'q_deg' in reply:
        tip, rot = fk(reply['q_deg'][:6])
        keep['tip_m'] = [round(float(v), 4) for v in tip]
        keep['approach'] = [round(float(v), 3) for v in -rot[:, 2]]
    print(json.dumps(keep))
    if not reply.get('ok', True):
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('cmd')
    p.add_argument('values', nargs='*', type=float)
    p.add_argument('--speed', type=float, default=5)
    p.add_argument('--pitch', type=float, default=0.)
    p.add_argument('--wait', type=float, default=4)
    p.add_argument('--dry', action='store_true')
    p.add_argument('--max-joint-step', type=float, default=25.)
    p.add_argument('--port', type=int, default=8765)
    a = p.parse_args()
    global PORT
    PORT = a.port
    if a.cmd == 'status':
        show(send({'cmd': 'status'}))
    elif a.cmd == 'fk':
        tip, rot = fk(a.values)
        print(json.dumps({'tip_m': tip.round(4).tolist(), 'approach': (-rot[:, 2]).round(3).tolist()}))
    elif a.cmd == 'joints':
        show(send({'cmd': 'move', 'q': a.values, 'speed': a.speed}))
    elif a.cmd in ('tip', 'nudge'):
        st = send({'cmd': 'status'})
        cur = st['q_deg'][:6]
        target = np.array(a.values)
        if a.cmd == 'nudge':
            target = fk(cur)[0] + target
        q, err = ik(target, cur, a.pitch)
        step = max(abs(x - y) for x, y in zip(q, cur))
        plan = {'target_m': target.round(4).tolist(), 'q_deg': [round(v, 2) for v in q],
                'ik_error_mm': round(err, 2), 'max_joint_step_deg': round(step, 2)}
        print(json.dumps(plan))
        if err > 3:
            sys.exit('IK error too large; not moving')
        if step > a.max_joint_step:
            sys.exit('joint step too large; use smaller increments')
        if not a.dry:
            show(send({'cmd': 'move', 'q': q, 'speed': a.speed}))
    elif a.cmd == 'grip':
        show(send({'cmd': 'grip', 'deg': a.values[0], 'wait': a.wait}))
    elif a.cmd == 'clear':
        show(send({'cmd': 'clear'}))
    elif a.cmd == 'rest':
        show(send({'cmd': 'rest'}))
    else:
        sys.exit(f'unknown command {a.cmd}')


if __name__ == '__main__':
    main()
