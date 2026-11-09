"""Dream drives one real Maker Arm at a time: live arm cameras + measured joints -> Dream chunk -> guarded moves.

Closed loop, one cycle at a time: read the active arm from pickup_server.py (127.0.0.1:8765), fetch the three arm
cameras from a dashboard (vision/live_view.py), call Dream with the prompt, and play waypoints of the returned chunk
through pickup_server's guarded moves (15 deg/s cap, temperature / torque / envelope guards that freeze, never drop).
The other arm stays unpowered; Dream still gets its measured joints.

--side auto (the default): a read-only `arm scan` reads both arms, the cooler one (below --start-below) is chosen,
pickup_server is started on it, and Saleh's prompt is mirrored to that side. If the arm reaches its heat limit, it
goes home and the remaining cycles continue on the other arm when that one is cool enough.
--side right|left: use a pickup_server you started yourself on that arm (pass the idle arm with --other-sdk).

Every exit returns the active arm home with pickup_server's `rest` (open jaws, slow return to the pose it was enabled
in, release): the last cycle, Ctrl-C, a guard trip, a motor at --temp-stop, or any error. Nothing bypasses a guard.

    ~/hub-mac/.venv/bin/python DreamSim/dream_arm.py --dry      # scan, call Dream, print the plan, no motion
    ~/hub-mac/.venv/bin/python DreamSim/dream_arm.py            # E-stop person at the stop
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from dream_live import JAW, SALEH_PROMPT, fetch_frames, obs_to_sdk, sdk_to_obs  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ADAPTERS = {'0027002E4146570520313238': 'right', '003A003F4146570320303038': 'left'}  # the station's adapters
SPEED = 15.0          # pickup_server's own cap
MAX_STEP_DEG = 30.0   # largest joint change sent in one move
# SDK profile maker_arm_v1.yaml joint limits (maker_arm_real_mapping.MOTOR_LIMITS_DEG); pickup_server refuses
# targets within 1 deg of them, so targets are clamped 2 deg inside.
LIMITS_DEG = ((-158.2, 156.1), (-175.3, -3.2), (2.8, 236.1), (-65.2, 104.0), (-97.6, 78.0), (-153.2, 152.0))


class Heat(RuntimeError):
    pass


def inside(q6, margin=2.0):
    return [min(hi - margin, max(lo + margin, v)) for v, (lo, hi) in zip(q6, LIMITS_DEG)]


def other_of(side):
    return 'left' if side == 'right' else 'right'


def mirror(prompt, side):
    """Saleh's prompt names the right arm; for the left arm swap right and left."""
    if side == 'right':
        return prompt
    return re.sub(r'\b(right|left)\b', lambda m: 'left' if m.group(1) == 'right' else 'right', prompt)


def server(cmd, port=8765, timeout=60):
    with socket.create_connection(('127.0.0.1', port), timeout=timeout) as s:
        s.sendall((json.dumps(cmd) + '\n').encode())
        return json.loads(s.makefile().readline())


def arm_env():
    env = dict(os.environ, PYTHONPATH=str(REPO / 'arm/tools'))
    if sys.platform == 'darwin':
        prefix = subprocess.run(['brew', '--prefix', 'libusb'], capture_output=True, text=True).stdout.strip()
        env['DYLD_FALLBACK_LIBRARY_PATH'] = prefix + '/lib'
    return env


def scan():
    """Read-only: {side: {'sdk': seven SDK degrees, 'max_temp': C}} for every arm that answered all seven motors."""
    out = subprocess.run(['bash', 'arm/tools/arm', 'scan'], cwd=REPO, capture_output=True, text=True, timeout=120).stdout
    arms, side = {}, None
    for line in out.splitlines():
        if line.startswith('adapter'):
            side = ADAPTERS.get(line.split()[1])
            continue
        m = re.match(r'\s+(\d)\s+\S+\s+([-+]?[0-9.]+)\s+([0-9.]+)', line)
        if m and side:
            arms.setdefault(side, []).append((float(m.group(2)), float(m.group(3))))
    return {s: {'sdk': [q for q, _ in v], 'max_temp': max(t for _, t in v)} for s, v in arms.items() if len(v) == 7}


def server_name(side):
    """The name pickup_server knows this arm by: arm_names.json, else arm-<last 4 of the adapter serial>."""
    serial = next(k for k, v in ADAPTERS.items() if v == side)
    names = json.loads((REPO / 'arm/tools/arm_names.json').read_text())
    return names.get(serial, f'arm-{serial[-4:]}')


def start_server(side, run, port):
    proc = subprocess.Popen([str(REPO / 'arm/.venv/bin/python'), 'arm/tools/pickup_server.py', server_name(side),
                             '--operator-cutoff',
                             '--log', str(run / f'server-{side}.jsonl'), '--port', str(port)],
                            cwd=REPO, env=arm_env(), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if line:
            print(f'  [{side} server] {line.rstrip()}', flush=True)
        if 'READY' in line:
            return proc
        if proc.poll() is not None:
            break
    proc.kill()
    raise RuntimeError(f'pickup_server on the {side} arm did not become READY')


def stop_server(proc, side):
    """After rest the server releases the arm and exits; never kill one that still holds an enabled arm."""
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            st = server({'cmd': 'status'})
        except OSError:
            st = {}
        if st.get('arm_state') == 'ENABLED':
            print(f'WARNING: {side} arm still enabled; leaving its server running (E-stop or `rest`).', flush=True)
            return
        proc.terminate()


def run_side(client, side, other_sdk, a, run, start_cycle, cycles, active_sdk=None):
    """Play up to `cycles` Dream cycles on `side`. Returns cycles completed; raises Heat on a heat stop."""
    key = 'q_r_chunk' if side == 'right' else 'q_l_chunk'
    prompt = mirror(a.prompt, side)
    print(f'{side} arm: prompt "{prompt}"', flush=True)
    status = (lambda: {'q_deg': active_sdk, 'temp_c': [0], 'trip': None}) if a.dry else (lambda: server({'cmd': 'status'}, a.port))
    done = restarts = 0
    try:
        for cycle in range(start_cycle, start_cycle + cycles):
            st = status()
            if st.get('trip'):
                raise (Heat if 'temperature' in st['trip'] else RuntimeError)(f'guard trip: {st["trip"]}')
            hot = max(t for t in st['temp_c'] if t is not None)
            if hot >= a.temp_stop:
                raise Heat(f'motor at {hot} C (stop at {a.temp_stop})')
            obs = {'prompt': prompt}
            for s, sdk7 in ((side, st['q_deg']), (other_of(side), other_sdk)):
                q, gap = sdk_to_obs(sdk7)
                obs[f'state.{s}_arm'] = np.asarray(q)
                obs[f'state.{s}_grip'] = [gap / JAW]
            (run / f'cycle{cycle}').mkdir(exist_ok=True)
            frames = fetch_frames(a.cams, run / f'cycle{cycle}')
            t0 = time.monotonic()
            try:
                client.set_observation(frames, obs)
                action = client.get_action()
            except DreamSessionExpired:
                # Dream ends a session left idle while the arm moved; the client has adopted the new one and the
                # arm is holding its pose under the guards, so ask again (Dream's documented recovery).
                print('  Dream session expired while the arm moved; continuing on the new session', flush=True)
                restarts += 1
                client.set_observation(frames, obs)
                action = client.get_action()
            dt = time.monotonic() - t0
            rows = [obs_to_sdk(r) for r in np.asarray(action[key], dtype=float)]
            picks = sorted({round(i) for i in np.linspace(len(rows) / a.waypoints - 1, len(rows) - 1, a.waypoints)})
            plan = [rows[i] for i in picks]
            print(f'cycle {cycle} ({side}): Dream {dt:.2f}s, {len(rows)} rows -> {plan[-1][:6]} grip {plan[-1][6]}'
                  f'  (temps max {hot})', flush=True)
            entry = {'cycle': cycle, 'side': side, 'prompt': prompt, 'dream_s': round(dt, 3), 'state_sdk': st['q_deg'],
                     'temps': st['temp_c'], 'plan_sdk': plan, 'chunk_len': len(rows),
                     'session_restarts': restarts}
            if not a.dry:
                for row in plan:
                    cur = server({'cmd': 'status'}, a.port)['q_deg']
                    goal = inside([c + max(-MAX_STEP_DEG, min(MAX_STEP_DEG, g - c)) for c, g in zip(cur[:6], row[:6])])
                    r = server({'cmd': 'move', 'q': goal, 'speed': SPEED, 'grip': max(-96.0, min(-2.5, row[6]))},
                               a.port, timeout=90)
                    if not r.get('ok'):
                        entry['refused'] = r.get('error')
                        print(f'  move refused: {r.get("error")}', flush=True)
                        if 'tripped' in str(r.get('error')):
                            raise (Heat if 'temperature' in str(r.get('error')) else RuntimeError)(r.get('error'))
                        break
                entry['after_sdk'] = server({'cmd': 'status'}, a.port)['q_deg']
            with (run / 'cycles.jsonl').open('a') as log:
                log.write(json.dumps(entry) + '\n')
            done += 1
    finally:
        if not a.dry:
            print(f'{side} arm returning home (rest: open jaws, slow return to the enable pose, release)', flush=True)
            try:
                print('  ' + json.dumps(server({'cmd': 'rest'}, a.port, timeout=120)), flush=True)
            except OSError as exc:
                print(f'  rest failed: {exc} - use the E-stop', flush=True)
    return done


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--prompt', default=SALEH_PROMPT, help="written for the right arm; mirrored for the left")
    p.add_argument('--side', choices=('auto', 'right', 'left'), default='auto')
    p.add_argument('--other-sdk', help='with --side right|left: seven measured SDK degrees of the idle arm')
    p.add_argument('--cams', default='http://127.0.0.1:8792', help='dashboard serving /cam/<role>.jpg')
    p.add_argument('--cycles', type=int, default=20)
    p.add_argument('--waypoints', type=int, default=1,
                   help='chunk rows played per cycle (evenly spaced); 1 keeps Dream asked every few seconds')
    p.add_argument('--temp-stop', type=float, default=50.0, help='return home when any motor reaches this')
    p.add_argument('--start-below', type=float, default=44.0, help='auto: only start an arm whose motors are cooler')
    p.add_argument('--checkpoint', default='dreamlabs/dream-1-beta')
    p.add_argument('--dry', action='store_true', help='call Dream and print the plan; send no motion')
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--out', type=Path, default=Path('~/hub-mac/dream-runs').expanduser())
    a = p.parse_args()
    run = a.out / time.strftime('arm-%Y%m%d-%H%M%S')
    run.mkdir(parents=True, exist_ok=True)

    global DreamSessionExpired
    from dream import DreamClient, DreamSessionExpired
    from model import prepare
    from probe import configure
    client = DreamClient()
    try:
        configure(client, prepare(run / 'model', tcp_back=0.), a.checkpoint)
        print('Dream policy configured; warming IK', flush=True)
        if not client.wait_for_ik(240):
            raise TimeoutError('Dream IK warmup exceeded 240 s')

        if a.side != 'auto':
            if not a.other_sdk:
                sys.exit('--side right|left needs --other-sdk')
            run_side(client, a.side, [float(v) for v in a.other_sdk.split()], a, run, 1, a.cycles)
            return

        remaining, cycle, hot_sides = a.cycles, 1, set()
        while remaining > 0:
            arms = scan()
            print('scan: ' + ', '.join(f'{s} max {v["max_temp"]:.0f} C' for s, v in sorted(arms.items())), flush=True)
            if len(arms) < 2:
                raise RuntimeError(f'need both arms answering for Dream state; got {sorted(arms)} (power? cables?)')
            ready = [s for s in arms if s not in hot_sides and arms[s]['max_temp'] < a.start_below]
            if not ready:
                print(f'no arm is below {a.start_below} C; let them cool and run again', flush=True)
                break
            side = min(ready, key=lambda s: arms[s]['max_temp'])
            other_sdk = arms[other_of(side)]['sdk']
            if a.dry:
                done = run_side(client, side, other_sdk, a, run, cycle, remaining, active_sdk=arms[side]['sdk'])
                break
            proc = start_server(side, run, a.port)
            try:
                done = run_side(client, side, other_sdk, a, run, cycle, remaining)
                remaining = 0
            except Heat as exc:
                done = None
                print(f'{side} arm heat stop: {exc}; switching to the other arm if it is cool', flush=True)
                hot_sides.add(side)
            finally:
                stop_server(proc, side)
            if done is None:
                logged = (run / 'cycles.jsonl').read_text().count('\n') if (run / 'cycles.jsonl').exists() else 0
                remaining, cycle = a.cycles - logged, logged + 1
    except KeyboardInterrupt:
        print('Ctrl-C (the active arm was sent home)', flush=True)
    except Exception as exc:
        print(f'stopping: {type(exc).__name__}: {exc}', flush=True)
    finally:
        client.close()
        print(f'log: {run}', flush=True)


if __name__ == '__main__':
    main()
