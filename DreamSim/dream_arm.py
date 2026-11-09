"""Dream drives one real Maker Arm: live arm cameras + measured joints -> Dream action chunk -> guarded moves.

Closed loop, one cycle at a time: read the active arm from pickup_server.py (127.0.0.1:8765), fetch the three arm
cameras from a dashboard (vision/live_view.py), call Dream with the prompt, and play waypoints of the returned chunk
through pickup_server's guarded moves (15 deg/s cap, temperature / torque / envelope guards that freeze, never drop).
The other arm stays unpowered: its state is the measured reading you pass with --other-sdk.

Every exit returns the arm home with pickup_server's `rest` (open jaws, slow return to the pose it was enabled in,
release): the last cycle, Ctrl-C, a guard trip, a motor at --temp-stop, or any error. Nothing here bypasses a guard.

    # 1. guarded arm server on the active arm (arm venv), E-stop person at the stop
    PYTHONPATH=arm/tools DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix libusb)/lib" arm/.venv/bin/python \
        arm/tools/pickup_server.py right --operator-cutoff --log arm/local/dream/server.jsonl
    # 2. Dream loop (Dream venv); --dry plans without moving
    ~/hub-mac/.venv/bin/python DreamSim/dream_arm.py --other-sdk "-6.3 -3.2 2.1 -5.0 -4.4 355.8 265.5" --dry
"""
import argparse
import json
import math
import socket
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dream_live import JAW, SALEH_PROMPT, fetch_frames, obs_to_sdk, sdk_to_obs  # noqa: E402

SPEED = 15.0          # pickup_server's own cap
MAX_STEP_DEG = 30.0   # largest joint change sent in one move
# SDK profile maker_arm_v1.yaml joint limits (maker_arm_real_mapping.MOTOR_LIMITS_DEG); pickup_server refuses
# targets within 1 deg of them, so targets are clamped 2 deg inside.
LIMITS_DEG = ((-158.2, 156.1), (-175.3, -3.2), (2.8, 236.1), (-65.2, 104.0), (-97.6, 78.0), (-153.2, 152.0))


def inside(q6, margin=2.0):
    return [min(hi - margin, max(lo + margin, v)) for v, (lo, hi) in zip(q6, LIMITS_DEG)]


def server(cmd, port=8765, timeout=60):
    with socket.create_connection(('127.0.0.1', port), timeout=timeout) as s:
        s.sendall((json.dumps(cmd) + '\n').encode())
        reply = json.loads(s.makefile().readline())
    return reply


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--prompt', default=SALEH_PROMPT)
    p.add_argument('--side', choices=('right', 'left'), default='right', help='the arm pickup_server holds')
    p.add_argument('--other-sdk', required=True, help='seven measured SDK degrees of the unpowered arm (arm scan)')
    p.add_argument('--cams', default='http://127.0.0.1:8792', help='dashboard serving /cam/<role>.jpg')
    p.add_argument('--cycles', type=int, default=6)
    p.add_argument('--waypoints', type=int, default=3, help='chunk rows played per cycle (evenly spaced)')
    p.add_argument('--temp-stop', type=float, default=50.0, help='return home when any motor reaches this')
    p.add_argument('--checkpoint', default='dreamlabs/dream-1-beta')
    p.add_argument('--dry', action='store_true', help='call Dream and print the plan; send no motion')
    p.add_argument('--active-sdk', help='with --dry and no arm server: seven measured SDK degrees of the active arm')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--out', type=Path, default=Path('~/hub-mac/dream-runs').expanduser())
    a = p.parse_args()

    other = a.other_sdk.split() if isinstance(a.other_sdk, str) else a.other_sdk
    other_sdk = [float(v) for v in other]
    other_side = 'left' if a.side == 'right' else 'right'
    key = 'q_r_chunk' if a.side == 'right' else 'q_l_chunk'
    run = a.out / time.strftime('arm-%Y%m%d-%H%M%S')
    run.mkdir(parents=True, exist_ok=True)
    log = (run / 'cycles.jsonl').open('w')

    def status():
        if a.dry and a.active_sdk:
            return {'ok': True, 'q_deg': [float(v) for v in a.active_sdk.split()], 'temp_c': [0], 'trip': None}
        return server({'cmd': 'status'}, a.port)

    st = status()
    if not a.dry or not a.active_sdk:
        if not st.get('ok') or st.get('trip') or st.get('arm_state') != 'ENABLED':
            sys.exit(f'pickup_server not ready: {st}')
    print(f'{a.side} q {st["q_deg"]}, temps {st["temp_c"]}', flush=True)

    from dream import DreamClient
    from model import prepare
    from probe import configure
    client = DreamClient()
    moved = False
    try:
        configure(client, prepare(run / 'model', tcp_back=0.), a.checkpoint)
        print('Dream policy configured; warming IK', flush=True)
        if not client.wait_for_ik(240):
            raise TimeoutError('Dream IK warmup exceeded 240 s')
        for cycle in range(1, a.cycles + 1):
            st = status()
            if st.get('trip'):
                raise RuntimeError(f'guard trip: {st["trip"]}')
            hot = max(t for t in st['temp_c'] if t is not None)
            if hot >= a.temp_stop:
                raise RuntimeError(f'motor at {hot} C (stop at {a.temp_stop})')
            obs = {'prompt': a.prompt}
            for side, sdk7 in ((a.side, st['q_deg']), (other_side, other_sdk)):
                q, gap = sdk_to_obs(sdk7)
                obs[f'state.{side}_arm'] = np.asarray(q)
                obs[f'state.{side}_grip'] = [gap / JAW]
            (run / f'cycle{cycle}').mkdir(exist_ok=True)
            frames = fetch_frames(a.cams, run / f'cycle{cycle}')
            t0 = time.monotonic()
            client.set_observation(frames, obs)
            action = client.get_action()
            dt = time.monotonic() - t0
            rows = [obs_to_sdk(r) for r in np.asarray(action[key], dtype=float)]
            picks = sorted({round(i) for i in np.linspace(len(rows) / a.waypoints, len(rows) - 1, a.waypoints)})
            plan = [rows[i] for i in picks]
            print(f'cycle {cycle}: Dream {dt:.2f}s, {len(rows)} rows, play {picks} -> '
                  f'{[r[:6] for r in plan][-1]} grip {plan[-1][6]}  (temps max {hot})', flush=True)
            entry = {'cycle': cycle, 'dream_s': round(dt, 3), 'state_sdk': st['q_deg'], 'temps': st['temp_c'],
                     'plan_sdk': plan, 'chunk_len': len(rows)}
            if not a.dry:
                for row in plan:
                    cur = server({'cmd': 'status'}, a.port)['q_deg']
                    goal = inside([c + max(-MAX_STEP_DEG, min(MAX_STEP_DEG, g - c)) for c, g in zip(cur[:6], row[:6])])
                    grip = max(-96.0, min(-2.5, row[6]))
                    r = server({'cmd': 'move', 'q': goal, 'speed': SPEED, 'grip': grip}, a.port, timeout=90)
                    moved = True
                    if not r.get('ok'):
                        entry['refused'] = r.get('error')
                        print(f'  move refused: {r.get("error")}', flush=True)
                        if r.get('trip') or 'tripped' in str(r.get('error')):
                            raise RuntimeError(f'guard trip: {r.get("error")}')
                        break
                entry['after_sdk'] = server({'cmd': 'status'}, a.port)['q_deg']
            log.write(json.dumps(entry) + '\n')
            log.flush()
    except KeyboardInterrupt:
        print('Ctrl-C', flush=True)
    except Exception as exc:
        print(f'stopping: {type(exc).__name__}: {exc}', flush=True)
    finally:
        client.close()
        log.close()
        if moved or not a.dry:
            print('returning home (rest: open jaws, slow return to the enable pose, release)', flush=True)
            print(json.dumps(server({'cmd': 'rest'}, a.port, timeout=120)), flush=True)
        print(f'log: {run}', flush=True)


if __name__ == '__main__':
    main()
