"""Supervised seven-joint holding controller driven over a localhost socket.

Enables one arm (joints 1-6 plus the gripper with the SDK's capped closing and
0.5 Nm holding regulation) and keeps it holding until a verified return. Each
client line is one JSON command; each reply is one JSON line:

  {"cmd": "status"}
  {"cmd": "move", "q": [six SDK joint degrees], "speed": deg/s}   # joint-space line
  {"cmd": "grip", "deg": gripper SDK degrees, "wait": seconds}
  {"cmd": "clear"}          # resume after an inspected trip (SDK still ENABLED)
  {"cmd": "rest"}           # open gripper, SDK verified return to the enable pose, release
  {"cmd": "release_supported"}   # only with the arm physically supported

A guard trip (stale feedback, temperature, speed, torque residual against the
gravity model, envelope) freezes the arm at its measured pose and refuses
further moves; it never drops an elevated arm. Experimental bench code.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import socketserver
import sys
import threading
import time

from arms import resolve
from gsusb_backend import GsUsbBackend
from maker_arm import gravity
from maker_arm.arm import Arm, ArmState
from maker_arm.config import ArmConfig
from maker_arm.profiles import DEFAULT_ARM_CONFIG

# Residual |measured - gravity model| torque that trips the guard, joints 1-6.
RESIDUAL_NM = (2.0, 5.0, 4.0, 2.0, 1.2, 1.2)
MAX_SPEED_DEG_S = 15.
MEASURED_SPEED_GUARD = 30.
TEMP_GUARD_C = 52.


def config():
    base = ArmConfig.from_yaml(str(DEFAULT_ARM_CONFIG))
    cfg = replace(base, control_rate_hz=25., max_velocity=math.radians(MAX_SPEED_DEG_S),
                  feedback_timeout=.25, hold_on_fault=True, rest_speed=math.radians(6),
                  rest_timeout=60., rest_settle=math.radians(4))
    cfg._validate()
    return cfg


class Controller:
    def __init__(self, arm, log_path):
        self.arm = arm
        self.lock = threading.RLock()
        self.trip = None
        self.log_path = log_path
        self.log = log_path.open('a')
        self.closed = False
        self.enabled_at = None

    def record(self, **event):
        event['t'] = time.time()
        self.log.write(json.dumps(event, default=str) + '\n')
        self.log.flush()

    def state(self):
        a = self.arm
        q = [math.degrees(p) for p in a.get_joint_positions()]
        tau = [m.feedback.torque if m.feedback else None for m in a.motors]
        g = gravity.gravity_nm(q[:6]) if all(math.isfinite(x) for x in q[:6]) else [math.nan] * 6
        return {'arm_state': a.state.name, 'fault': a.fault_reason, 'trip': self.trip,
                'q_deg': [round(x, 3) for x in q],
                'cmd_deg': [round(math.degrees(x), 3) for x in a.get_commanded_positions()],
                'torque_nm': [round(t, 3) if t is not None else None for t in tau],
                'gravity_nm': [round(x, 3) for x in g],
                'temp_c': [m.feedback.temperature if m.feedback else None for m in a.motors],
                'age_s': [round(m.feedback_age, 3) for m in a.motors]}

    def check(self):
        a = self.arm
        if a.state is not ArmState.ENABLED:
            return f'arm state {a.state.name}: {a.fault_reason}'
        q = [math.degrees(p) for p in a.get_joint_positions()]
        g = gravity.gravity_nm(q[:6])
        for i, m in enumerate(a.motors):
            fb = m.feedback
            if fb is None or m.feedback_age > .25:
                return f'motor {i+1} stale feedback'
            if fb.temperature >= TEMP_GUARD_C:
                return f'motor {i+1} temperature {fb.temperature}'
            # Gripper closing is rate-limited and effort-capped by the SDK; its
            # reported velocity spikes during capped closing, so it is not speed-guarded.
            if i < 6 and abs(math.degrees(fb.velocity)) > MEASURED_SPEED_GUARD:
                return f'motor {i+1} speed {math.degrees(fb.velocity):.1f} deg/s'
            # The SDK eases gravity feed-forward in over 0.5 s after enable.
            settled = self.enabled_at is not None and time.monotonic() - self.enabled_at > 2.
            if settled and i < 6 and abs(fb.torque - g[i]) > RESIDUAL_NM[i]:
                return f'motor {i+1} torque residual {fb.torque:.2f} vs model {g[i]:.2f} Nm'
        return None

    def freeze(self, reason):
        self.trip = reason
        self.arm.hold_current_position()
        self.record(event='trip', reason=reason, state=self.state())

    def monitor(self):
        while not self.closed:
            with self.lock:
                if self.trip is None and self.arm.state in (ArmState.ENABLED, ArmState.FAULT):
                    reason = self.check()
                    if reason:
                        self.freeze(reason)
            time.sleep(.04)

    def move(self, q_goal, speed, grip=None):
        a = self.arm
        if self.trip:
            raise RuntimeError(f'tripped: {self.trip}')
        if len(q_goal) != 6 or not all(math.isfinite(x) for x in q_goal):
            raise ValueError('need six finite joint degrees')
        speed = min(MAX_SPEED_DEG_S, max(1., float(speed)))
        for j, x in zip(a.config.joints, q_goal):
            if not math.degrees(j.lo) + 1 <= x <= math.degrees(j.hi) - 1:
                raise ValueError(f'{j.name} target {x:.1f} outside limits')
        start = [math.degrees(p) for p in a.get_joint_positions()]
        cmd = [math.degrees(p) for p in a.get_commanded_positions()]
        g7 = cmd[6] if grip is None else grip
        dist = max(abs(g - s) for g, s in zip(q_goal, start[:6]))
        steps = max(1, math.ceil(dist / speed * 25))
        self.record(event='move', start=start, goal=q_goal, speed=speed)
        for k in range(1, steps + 1):
            f = k / steps
            target = [s + (g - s) * f for s, g in zip(start[:6], q_goal)] + [g7]
            with self.lock:
                if self.trip:
                    raise RuntimeError(f'tripped: {self.trip}')
                if not a.set_joint_targets([math.radians(x) for x in target]):
                    self.freeze('target rejected')
                    raise RuntimeError('target rejected')
            time.sleep(.04)
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if self.trip:
                raise RuntimeError(f'tripped: {self.trip}')
            q = [math.degrees(p) for p in a.get_joint_positions()]
            err = max(abs(x - g) for x, g in zip(q[:6], q_goal))
            if err <= 1.5:
                break
            time.sleep(.1)
        st = self.state()
        st['max_error_deg'] = round(max(abs(x - g) for x, g in zip(st['q_deg'][:6], q_goal)), 3)
        self.record(event='moved', state=st)
        return st

    def grip(self, deg, wait):
        a = self.arm
        if self.trip:
            raise RuntimeError(f'tripped: {self.trip}')
        j = a.config.joints[6]
        deg = min(math.degrees(j.hi), max(math.degrees(j.lo), float(deg)))
        cmd = [math.degrees(p) for p in a.get_commanded_positions()]
        with self.lock:
            if not a.set_joint_targets([math.radians(x) for x in cmd[:6] + [deg]]):
                raise RuntimeError('target rejected')
        time.sleep(min(15., max(.5, float(wait))))
        st = self.state()
        hold = a._hold.get(6)
        st['gripper_holding'] = bool(hold and hold.holding)
        self.record(event='grip', goal=deg, state=st)
        return st

    def rest(self):
        a = self.arm
        with self.lock:
            j = a.config.joints[6]
            cmd = [math.degrees(p) for p in a.get_commanded_positions()]
            if a.state is ArmState.ENABLED:
                a.set_joint_targets([math.radians(x) for x in cmd[:6] + [math.degrees(j.lo)]])
        time.sleep(3)
        with self.lock:
            ok = bool(a.stop())
            st = self.state()
            st['return_verified'] = ok
            st['release_acknowledged'] = ok and not a.unconfirmed_disable
            self.record(event='rest', state=st)
            if st['release_acknowledged']:
                a.disconnect()
                self.closed = True
        return st

    def release_supported(self):
        with self.lock:
            self.arm.disable()
            st = {'release_acknowledged': not self.arm.unconfirmed_disable}
            self.record(event='release_supported', state=st)
            if st['release_acknowledged']:
                self.arm.disconnect()
                self.closed = True
        return st


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('arm')
    p.add_argument('--operator-cutoff', action='store_true', required=True)
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--log', type=Path, required=True)
    args = p.parse_args()
    name, serial = resolve(args.arm)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    arm = Arm(config(), GsUsbBackend(serial))
    arm.connect()
    ctl = Controller(arm, args.log)
    ctl.record(event='connect', arm=name, serial=serial, when=datetime.now(timezone.utc).isoformat())
    arm.enable()
    ctl.enabled_at = time.monotonic()
    ctl.record(event='enabled', state=ctl.state())
    threading.Thread(target=ctl.monitor, daemon=True).start()

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            for line in self.rfile:
                try:
                    c = json.loads(line)
                    kind = c.get('cmd')
                    if kind == 'status':
                        out = ctl.state()
                    elif kind == 'move':
                        out = ctl.move(c['q'], c.get('speed', 5), c.get('grip'))
                    elif kind == 'grip':
                        out = ctl.grip(c['deg'], c.get('wait', 4))
                    elif kind == 'clear':
                        # Operator inspected a held trip; the SDK must still be ENABLED.
                        if arm.state is not ArmState.ENABLED:
                            raise RuntimeError(f'cannot clear in {arm.state.name}')
                        ctl.record(event='clear', reason=ctl.trip)
                        ctl.trip = None
                        out = ctl.state()
                    elif kind == 'rest':
                        out = ctl.rest()
                    elif kind == 'release_supported':
                        out = ctl.release_supported()
                    else:
                        raise ValueError(f'unknown command {kind}')
                    reply = {'ok': True, **out}
                except Exception as exc:
                    reply = {'ok': False, 'error': f'{type(exc).__name__}: {exc}', **ctl.state()}
                self.wfile.write((json.dumps(reply, default=str) + '\n').encode())
                if ctl.closed:
                    threading.Thread(target=server.shutdown, daemon=True).start()
                    return

    socketserver.TCPServer.allow_reuse_address = True
    server = socketserver.TCPServer(('127.0.0.1', args.port), Handler)
    print(f'READY {name} port {args.port}', flush=True)
    try:
        server.serve_forever()
    finally:
        if not ctl.closed:
            # Never drop an elevated arm on server exit: keep holding until released.
            print('SERVER_EXIT_WITH_ARM_ENABLED: holding; use physical cutoff', flush=True)
            ctl.record(event='server_exit_holding', state=ctl.state())
            while True:
                time.sleep(1)


if __name__ == '__main__':
    sys.exit(main())
