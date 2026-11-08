#!/usr/bin/env python3
"""Headless Table 10 real-arm service: real_arm_bridge.Bridge without a terminal.

Started and supervised by DreamSim/table10_agent.py while the editor runs, with the
maker-arm-sdk venv python (local gs_usb backend):

  <maker-arm-sdk>/.venv/bin/python Tools/real_arm_service.py --left SERIAL --right SERIAL \
      --state-dir <project>/Saved/RealArmLink

Control comes from a heartbeat file written by the agent (from the web page / VR):
  control.json  {"unix": t, "drive": bool, "drive_seq": n, "release_seq": n, "source": "web"}
Status goes to service.json every 0.25 s from its own thread, so the page is never stale.

States ("mode" in service.json):
  off        No motor answers (power off, E-stop pressed, cable). Torque is off. Reconnects by itself.
  mirror     Torque off. Table 10 copies the real arms. Drive can be switched on.
  drive      Torque on. The real arms follow Table 10.
  returning  Turning off: the arms return to their engage pose, then torque is released.
  hold       An arm could not verify its return (a fault) and keeps holding its pose so it does
             not fall. Support it, then press Release now (torque off immediately).

Lessons from the first hardware session (2026-11-08), built in here:
- Torque off must never wait. The terminal bridge's release() blocked on a typed RELEASE after a
  fault, so the web button did nothing and the page went stale. Here turning off never blocks:
  a holding arm is reported, and Release now uses the SDK's thread-safe estop().
- Power off is torque off. When no motor has answered for POWER_LOST_S the state becomes 'off',
  drive is dropped, and the service reconnects as soon as the arms answer again.
- CAN faults ("no fault-status reply" at engage). Drive is refused unless every motor answers
  PREFLIGHT_READS clean reads, drive runs at DRIVE_HZ (half the bridge's 50 Hz) to lower bus
  load, and any fault in drive returns the arms instead of continuing.
All of real_arm_bridge.Bridge's own guards still apply (engage only within 5 deg of Unreal,
0.6 rad/s cap, jump rejection, stale-Unreal hold, verified return before release).
Joints 1-6 have no motor watchdog: keep the hardware E-stop within reach whenever drive is on.
"""
import argparse
import json
import math
import signal
import sys
import threading
import time
from pathlib import Path

import real_arm_bridge as rb
import maker_arm_real_mapping as mapping
import maker_arm_real_tests as tests
from maker_arm.arm import ArmState
from maker_arm import protocol

DEADMAN_S = 2.0          # drive needs a control heartbeat at least this fresh
UE_LOST_S = 5.0          # drive needs Unreal state within this
POWER_LOST_S = 2.0       # no motor answered for this long -> arms are unpowered -> 'off'
RECONNECT_S = 3.0
PREFLIGHT_READS = 8
# The SDK's own control loop rate. The bridge forced 50 Hz; at that rate a motor's feedback lapsed past the SDK's
# 0.25 s watchdog right after enable, while the SDK's `maker-arm check` (default 200 Hz) held all seven motors and
# moved the same elbow cleanly on the same hardware (2026-11-08). Keep the SDK default.
SDK_CONTROL_HZ = 200.0
DRIVE_HZ = 50.0          # how often this service forwards Unreal targets to the SDK (not the motor loop)
ENGAGE_WINDOW_S = 10.0   # retry a refused engage (e.g. Table 10 still easing back after a preview)
ENGAGE_RETRY_S = 1.5
ACTIVE = (ArmState.ENABLED, ArmState.FAULT)
AT_ENGAGE_DEG = 2.0      # a faulted arm still this close to its engage pose is released (it is where
                         # the verified return would put it anyway)
# macOS throttles launchd jobs without ProcessType Interactive: a 5 ms sleep overran by ~35 ms (median) under the
# login agent vs ~7 ms from a terminal (2026-11-08). The SDK's 200 Hz loop then stalled past its 0.25 s feedback
# watchdog right after torque on, on both arms. Drive is refused while the timer lags this much.
TIMER_LAG_MAX_MS = 15.0
# Web control without Unreal. Torque on holds the arms where they are; the web tests (Move up, Go to idle)
# are planned and run here on the real arms. While the editor link runs it mirrors an arm driven from the web,
# and takes over driving (Unreal/VR handle drags) once it matches that arm for SYNC_S.
SYNC_S = 0.5
SYNC_DEG = 3.0
TEST_FRESH_S = 15.0
LAG_ABORT_DEG, LAG_ABORT_S = 12.0, 1.0
MOUNT_YAW = math.pi                                      # maker_arm_station.MOUNT_YAW (both sides)
BASES = {'left': (20.0, -40.0, 76.0), 'right': (20.0, 40.0, 76.0)}  # station-local cm; tests are relative moves


def timer_lag_ms(samples=40, period=0.005):
    """Median overshoot of a short sleep: how late this process's control loop would run."""
    over = []
    for _ in range(samples):
        t = time.perf_counter()
        time.sleep(period)
        over.append((time.perf_counter() - t - period) * 1000.)
    over.sort()
    return round(over[len(over) // 2], 1)


class Service:
    def __init__(self, args):
        self.args = args
        rb.DRIVE_HZ = SDK_CONTROL_HZ  # Bridge.engage() sets the SDK control_rate_hz from this module value
        self.bridge = rb.Bridge(args)
        self.dir = Path(args.state_dir)
        # One process per arm (two USB-CAN adapters in one process lost motor feedback right after enable,
        # while one adapter per process drives cleanly): per-arm state dir, shared Unreal state and control.
        if getattr(args, 'ue_state', None):
            self.bridge.ue_path = Path(args.ue_state)
        self.control_path = Path(args.control) if getattr(args, 'control', None) else self.dir / 'control.json'
        self.status_path = self.dir / 'service.json'
        self.control = {}
        self.mode, self.message, self.holding = 'off', 'starting', []
        self.release_seq = self.drive_seq = None
        self.engage_since, self.engage_last = None, 0.
        self.ue_seen = time.monotonic()
        self.lock = threading.RLock()
        self.stop_requested = False
        self.engage_pose = {}
        self.offline = []
        self.all_sides = list(self.bridge.sides)
        self.last_bus_log = 0.
        self.exited = False
        self.timer_ms = timer_lag_ms()
        print(f'timer lag {self.timer_ms} ms (5 ms sleep, median overshoot)', flush=True)
        self.limits = mapping.drive_limits()
        self.source, self.sync_since = 'web', None   # who drives an engaged arm: 'web' (this service) or 'unreal'
        self.motion, self.test, self.test_seen = None, None, None
        self.ack = None  # what this arm did with the latest web on/off request (shown step by step on the page)

    # ---- status / control threads --------------------------------------------------------
    def set(self, mode, message):
        if (mode, message) != (self.mode, self.message):
            print(f'[{mode}] {message}', flush=True)
        self.mode, self.message = mode, message

    def status_loop(self):
        while not self.exited:
            sides = self.all_sides
            answering = {s.name: self.side_answering(s) for s in sides}
            bus = self.bus_stats()
            rb.write(self.status_path, {'unix': time.time(), 'running': True, 'mode': self.mode, 'message': self.message,
                                        'holding': self.holding, 'answering': answering, 'bus': bus,
                                        'timer_ms': self.timer_ms,
                                        'source': self.source if self.mode == 'drive' else None, 'test': self.test,
                                        'ack': self.ack,
                                        'driving': [s.name for s in self.bridge.sides] if self.mode == 'drive' else [],
                                        'faults': {s.name: s.arm.fault_reason for s in sides if s.arm.fault_reason}})
            now = time.monotonic()
            if self.mode in ('drive', 'returning', 'hold') and now - self.last_bus_log > 1.0:
                self.last_bus_log = now
                print('[bus] ' + '  '.join(f"{k}: err {v['error_frames']} echo {v['echo_frames']} oldest {v['oldest_ms']} ms"
                                           for k, v in bus.items()), flush=True)
            time.sleep(.25)
        rb.write(self.status_path, {'unix': time.time(), 'running': False, 'mode': 'off', 'message': 'service stopped'})

    def control_loop(self):
        while not self.exited:
            try:
                self.control = json.loads(self.control_path.read_text())
            except (FileNotFoundError, ValueError, OSError):
                pass
            seq = self.control.get('release_seq')
            if self.release_seq is None or seq is None or seq < self.release_seq:
                self.release_seq = seq  # first read, or the web server restarted (its counter starts again)
            elif seq > self.release_seq:
                self.release_seq = seq
                self.release_now('Release now pressed')
            time.sleep(.1)

    # ---- arm helpers ---------------------------------------------------------------------
    def near_engage(self, side):
        start = self.engage_pose.get(side.name)
        if not start:
            return False
        try:
            now = side.arm.get_joint_positions()[:6]
            return max(abs(math.degrees(a - b)) for a, b in zip(now, start[:6])) <= AT_ENGAGE_DEG
        except Exception:
            return False

    def release_side(self, side):
        """Release one arm; returns False if it must keep holding (a verified return failed away from its engage pose)."""
        if side.arm.state not in ACTIVE:
            return True
        if not self.side_answering(side):
            side.arm.disable()  # unpowered: there is no torque to hold
            return True
        if side.arm.stop():
            return True
        if self.near_engage(side):
            print(f'[{side.name}] return not verified but the arm is at its engage pose: releasing', flush=True)
            side.arm.disable()
            return True
        return False

    def bus_stats(self):
        out = {}
        for side in self.all_sides:
            backend = getattr(side.arm, '_backend', None)
            try:
                ages = [m.feedback_age for m in side.arm.motors]
                oldest = max(ages)
            except Exception:
                oldest = float('inf')
            motor_faults = {}
            try:
                for joint, word in zip(side.arm.config.joints, side.arm.get_faults()):
                    if word:
                        motor_faults[joint.label] = protocol.fault_text(word)
            except Exception:
                pass
            out[side.name] = {'error_frames': getattr(backend, 'error_frames', None), 'echo_frames': getattr(backend, 'echo_frames', None),
                              'oldest_ms': None if oldest == float('inf') else int(1000 * oldest), 'motor_faults': motor_faults}
        return out

    @staticmethod
    def side_answering(side):
        try:
            return min(m.feedback_age for m in side.arm.motors) < POWER_LOST_S
        except (AttributeError, ValueError):
            return False

    def connect(self):
        for side in self.bridge.sides:
            try:
                side.arm.disconnect()
            except Exception:
                pass
        # Fresh Arm objects (a closed or faulted backend cannot simply reconnect); each arm on its
        # own, so one powered arm works while the other is off.
        connected, errors = [], []
        for name, serial in (('left', self.args.left), ('right', self.args.right)):
            if not serial:
                continue
            side = rb.Side(name, serial)
            try:
                side.arm.connect()
                connected.append(side)
            except Exception as error:
                errors.append(f"{name}: {str(error).splitlines()[0][:110]}")
                try:
                    side.arm.disconnect()
                except Exception:
                    pass
        self.offline = errors
        if not connected:
            raise RuntimeError('; '.join(errors))
        self.bridge.sides = connected
        self.all_sides = list(connected)
        self.bridge.mode = 'mirror'
        self.holding = []

    def reconnect_offline(self):
        """Pick up an arm that was not answering, without touching the connected one."""
        serials = {'left': self.args.left, 'right': self.args.right}
        still = []
        for entry in self.offline:
            name = entry.split(':', 1)[0]
            side = rb.Side(name, serials[name])
            try:
                side.arm.connect()
            except Exception as error:
                still.append(f"{name}: {str(error).splitlines()[0][:110]}")
                try:
                    side.arm.disconnect()
                except Exception:
                    pass
                continue
            self.all_sides = sorted(self.all_sides + [side], key=lambda s: s.name != 'left')
            self.bridge.sides = list(self.all_sides)
            print(f'[{name}] arm answering again: connected (torque off)', flush=True)
        self.offline = still
        self.set('mirror', 'arms connected (torque off)' + ('; not answering: ' + '; '.join(still) if still else ''))

    def power_lost(self, why):
        self.end_test('aborted', why)
        self.bridge.sides = list(self.all_sides)
        for side in self.bridge.sides:
            try:
                side.arm.disconnect()  # nothing is powered; just release the bus
            except Exception:
                pass
        self.bridge.mode, self.holding = 'mirror', []
        self.set('off', why + ': torque is off; reconnecting when the arms answer')
        self.acknowledge('off', why + ': no power, so no torque')

    def release_now(self, why):
        """Torque off immediately on every powered arm (SDK estop: safe from any thread)."""
        with self.lock:
            for side in self.bridge.sides:
                if side.arm.state in ACTIVE:
                    side.arm.estop()
            self.bridge.mode, self.holding = 'mirror', []
            self.bridge.sides = list(self.all_sides)
            self.end_test('aborted', why)
            if self.mode == 'off':  # nothing is powered: stay off and keep reconnecting
                self.acknowledge('off', why + ': arm not connected, no torque')
                return
            self.set('mirror', why + ': torque off')
            self.acknowledge('off', why + ': torque released immediately')

    def release_all(self, why):
        """Return each arm to its engage pose and release it; a failed return holds (never blocks)."""
        self.end_test('aborted', why)
        with self.lock:
            self.set('returning', why + ': returning the arms, then releasing torque')
            holding = [side.name for side in self.bridge.sides if not self.release_side(side)]
            self.bridge.mode = 'mirror'
            if not holding:
                self.bridge.sides = list(self.all_sides)  # back to mirroring both arms
            self.holding = holding
            if holding:
                reason = '; '.join(f'{s.name}: {s.arm.fault_reason}' for s in self.bridge.sides if s.name in holding and s.arm.fault_reason)
                self.set('hold', f"{' and '.join(holding)} arm HOLDING its pose ({reason or 'return not verified'}). "
                                 'Support it, then press Release now')
                self.acknowledge('holding', self.message)
            else:
                self.set('mirror', 'torque off')
                self.acknowledge('off', f'{why}: returned to its start pose, torque released')

    def engage_here(self):
        """Torque on where the arms are, without Unreal. The editor link (if running) mirrors them, then takes over."""
        for side in self.bridge.sides:
            cfg = side.arm.config
            cfg.control_rate_hz = SDK_CONTROL_HZ
            cfg.max_velocity = self.args.max_velocity
            side.arm.enable()
            meas = side.arm.get_joint_positions()
            side.gripper_hold = meas[6]
            side.last_target = list(meas)
            side.arm.set_joint_targets(side.last_target)
            side.warned.clear()
        self.bridge.mode, self.bridge.last_ue_seq = 'web', None
        self.source, self.sync_since = 'web', None
        print(f"DRIVE ON ({', '.join(s.name for s in self.bridge.sides)}): holding; web tests or Unreal drive it", flush=True)
        return True

    def try_engage(self):
        try:
            ok = self.engage_here()
        except Exception as error:  # e.g. a motor reports a latched fault at enable
            print(f'engage failed: {error}', flush=True)
            for side in self.bridge.sides:
                if side.arm.state in ACTIVE:
                    side.arm.disable()  # it has not moved: still at the pose it was enabled in
            self.bridge.mode = 'mirror'
            return False
        if ok:
            self.engage_pose = {s.name: list(s.last_target or []) for s in self.bridge.sides}
        return ok

    def preflight(self):
        self.timer_ms = timer_lag_ms()
        if self.timer_ms > TIMER_LAG_MAX_MS:
            return (f'macOS is throttling the arm service (5 ms timer runs {self.timer_ms} ms late), so the motor loop '
                    'would stall: reinstall the Table 10 agent (zsh DreamSim/install_table10_agent.sh)')
        for _ in range(PREFLIGHT_READS):
            for side in self.bridge.sides:
                fresh = side.arm.refresh(wait=True, timeout=0.05)
                if not fresh or not all(fresh):
                    missing = [side.arm.config.joints[i].label for i, ok in enumerate(fresh or []) if not ok]
                    return f"{side.name} arm: {', '.join(missing) or 'motors'} not answering reliably (check power/cables)"
        return None

    # ---- drive: web tests, hold, and the hand-over to Unreal ----------------------------------
    def acknowledge(self, state, detail):
        self.ack = {'seq': self.drive_seq, 'state': state, 'detail': str(detail)[:200], 'unix': time.time()}

    def drive_message(self):
        names = ' and '.join(s.name for s in self.bridge.sides)
        if self.motion:
            return f'torque on: {names} arm running the web test "{self.motion["action"]}"'
        if self.source == 'unreal':
            return f'torque on: {names} arm follows Table 10 (Unreal/VR)'
        return f'torque on: {names} arm holding; use the web tests' + (
            ' (Unreal is syncing to it)' if self.bridge.read_ue() is not None else ' (no Unreal needed)')

    def to_web(self):
        self.source, self.sync_since = 'web', None
        self.bridge.mode = 'web'  # the editor link mirrors this arm instead of driving it

    def ue_matches(self, ue):
        for side in self.bridge.sides:
            arm = (ue.get('arms') or {}).get(side.name) or {}
            if not arm.get('q') or not side.last_target:
                return False
            target = mapping.urdf_to_motor(arm['q'])
            if max(abs(math.degrees(a - b)) for a, b in zip(target, side.last_target[:6])) > SYNC_DEG:
                return False
        return True

    def handle_test(self, test, now):
        if not test or test.get('id') == self.test_seen:
            return
        self.test_seen = test.get('id')
        if time.time() - float(test.get('unix', 0)) > TEST_FRESH_S:
            return
        action = test.get('action')
        if action == 'stop':
            detail = 'held at the current pose' if self.motion else 'nothing was moving'
            if self.motion:
                self.hold_here()
                self.end_test('stopped', 'Stop test pressed')
                self.set('drive', self.drive_message())
            self.test = {**self.test_record(test), 'state': 'stopped', 'detail': detail}  # reported under the stop's id
            return
        names = [s.name for s in self.bridge.sides]
        try:
            q_now = {s.name: mapping.motor_to_urdf(s.last_target[:6]) for s in self.bridge.sides}
            gaps = {s.name: mapping.gripper_to_gap(s.last_target[6]) for s in self.bridge.sides}
            plan = tests.plan(action, q_now, gaps, {n: MOUNT_YAW for n in names}, {n: BASES[n] for n in names},
                              self.limits, sides=names)
        except (ValueError, KeyError, TypeError) as error:
            self.test = {**self.test_record(test), 'state': 'rejected', 'detail': str(error)[:200]}
            print(f'test {action} rejected: {error}', flush=True)
            return
        self.to_web()
        self.motion = {'id': test.get('id'), 'action': action, 'plan': plan, 't': 0.0, 'last': now,
                       'lag': 0.0, 'lag_since': None}
        self.test = {**self.test_record(test), 'state': 'running', 'progress': 0.0,
                     'detail': f"real arm moving ({plan['travel_deg']} deg in {plan['duration_s']:.1f} s)"}
        print(f"test {action}: {plan['travel_deg']} deg over {plan['duration_s']:.1f} s", flush=True)
        self.set('drive', self.drive_message())

    def test_record(self, test):
        return {'id': test.get('id'), 'action': test.get('action'), 'side': '/'.join(s.name for s in self.bridge.sides),
                'unix': time.time(), 'progress': 0.0, 'lag_deg': 0.0}

    def end_test(self, state, detail, test=None):
        if self.motion or test:
            record = self.test if self.test and self.test.get('state') == 'running' else self.test_record(test or self.motion)
            self.test = {**record, 'state': state, 'detail': str(detail)[:200], 'unix': time.time()}
            print(f'test {record.get("action")}: {state} ({detail})', flush=True)
        self.motion = None

    def hold_here(self):
        for side in self.bridge.sides:
            if side.arm.state is ArmState.ENABLED:
                meas = side.arm.get_joint_positions()
                target = list(meas[:6]) + [side.gripper_hold]
                if side.arm.set_joint_targets(target):
                    side.last_target = target

    def step_motion(self, now):
        m = self.motion
        m['t'] = min(m['plan']['duration_s'], m['t'] + min(0.1, now - m['last']))  # a hitch pauses, never jumps
        m['last'] = now
        lag = 0.0
        for side, q in tests.sample(m['plan'], m['t']).items():
            arm = next((s for s in self.bridge.sides if s.name == side), None)
            if arm is None or arm.arm.state is not ArmState.ENABLED:
                continue
            target = mapping.urdf_to_motor(q) + [arm.gripper_hold]
            if arm.arm.set_joint_targets(target):
                arm.last_target = target
            meas = arm.arm.get_joint_positions()
            lag = max(lag, max(abs(math.degrees(a - b)) for a, b in zip(target[:6], meas[:6])))
        m['lag'] = max(m['lag'], lag)
        progress = m['t'] / m['plan']['duration_s']
        self.test.update(progress=round(progress, 3), lag_deg=round(m['lag'], 1))
        if lag > LAG_ABORT_DEG:
            m['lag_since'] = m['lag_since'] or now
            if now - m['lag_since'] > LAG_ABORT_S:
                self.hold_here()
                self.end_test('aborted', f'real arm stayed {lag:.0f} deg behind the plan: holding where it is')
                self.set('drive', self.drive_message())
                return
        else:
            m['lag_since'] = None
        if progress >= 1.0:
            m.setdefault('end_at', now)
            if lag < 2.0 or now - m['end_at'] > 1.5:  # let the arm settle onto the last target
                self.end_test('done', f'real arm moved; max lag {m["lag"]:.1f} deg, final {lag:.1f} deg')
                self.set('drive', self.drive_message())

    def drive_tick(self, now, test):
        self.handle_test(test, now)
        if self.motion:
            self.step_motion(now)
        elif self.source == 'unreal':
            if self.bridge.read_ue() is None:
                self.to_web()  # editor gone: hold here, keep torque, web control continues
                print('Unreal state lost: holding; web control continues', flush=True)
                self.set('drive', self.drive_message())
            else:
                self.bridge.drive_step()
                return
        else:
            ue = self.bridge.read_ue()
            if ue is not None and self.ue_matches(ue):
                self.sync_since = self.sync_since or now
                if now - self.sync_since >= SYNC_S:
                    self.source, self.bridge.mode, self.bridge.last_ue_seq = 'unreal', 'drive', None
                    print('Unreal matches the real arm: Unreal/VR drives it now', flush=True)
                    self.set('drive', self.drive_message())
                    self.bridge.drive_step()
                    return
            else:
                self.sync_since = None
        arms = {}
        for side in self.bridge.sides:
            m = self.bridge.measured(side)
            if m:
                arms[side.name] = m
        self.bridge.publish(arms)

    def publish_measured(self):
        arms = {}
        for side in self.bridge.sides:
            m = self.bridge.measured(side)
            if m:
                arms[side.name] = m
        mode, self.bridge.mode = self.bridge.mode, 'hold'  # the link shows the held pose and refuses tests
        self.bridge.publish(arms)
        self.bridge.mode = mode

    # ---- main loop -----------------------------------------------------------------------
    def step(self, now):
        c = self.control
        fresh = time.time() - float(c.get('unix', 0)) <= DEADMAN_S
        if self.bridge.read_ue() is not None:
            self.ue_seen = now
        if self.mode == 'off':
            if c.get('drive') and fresh and c.get('drive_seq') != self.drive_seq:
                self.drive_seq = c.get('drive_seq')  # consumed: the arm will not switch on by itself later
                self.acknowledge('refused', 'arm not connected (power off, E-stop pressed or USB unplugged)')
                print('drive request refused: arm not connected', flush=True)
            try:
                self.connect()
                self.set('mirror', 'arms connected (torque off)' + ('; not answering: ' + '; '.join(self.offline) if self.offline else ''))
            except Exception as error:
                detail = str(error).splitlines()[0][:160]
                if detail != getattr(self, 'last_connect_error', None):
                    self.last_connect_error = detail
                    print(f'connect failed: {detail}', flush=True)
                self.set('off', 'arms not answering (power off, E-stop or cable?): torque is off; reconnecting. ' + detail)
                time.sleep(RECONNECT_S)
            return 0.
        if self.mode == 'mirror':
            if c.get('drive') and fresh and c.get('drive_seq') != self.drive_seq:
                self.drive_seq = c.get('drive_seq')
                self.engage_since, self.engage_last = now, 0.
                self.acknowledge('received', 'request received; checking every motor answers')
                print(f"drive requested from {c.get('source', 'web')}", flush=True)
            if c.get('drive') and fresh and self.engage_since is not None and now - self.engage_last >= ENGAGE_RETRY_S:
                if now - self.engage_since > ENGAGE_WINDOW_S:
                    self.engage_since = None
                    self.set('mirror', 'drive not engaged: ' + self.message)
                    self.acknowledge('refused', self.message)
                else:
                    self.engage_last = now
                    wanted = c.get('sides') or ['left', 'right']
                    mine = [s for s in self.all_sides if s.name in wanted]
                    if not mine:  # this process's arm was not selected: stay in mirror
                        self.engage_since = None
                        self.acknowledge('skipped', 'not selected')
                        arms = self.bridge.mirror_step()
                        return 1.0 / rb.MIRROR_HZ
                    self.bridge.sides = mine
                    reason = self.preflight()
                    if reason:
                        self.bridge.sides = list(self.all_sides)
                        self.set('mirror', 'drive refused: ' + reason)
                        self.acknowledge('retrying', reason)
                    elif self.try_engage():
                        self.engage_since = None
                        self.set('drive', self.drive_message())
                        self.acknowledge('on', 'motors enabled (torque on) and holding their pose')
                        return 1.0 / DRIVE_HZ
                    else:
                        self.bridge.sides = list(self.all_sides)
                        self.set('mirror', 'drive refused: ' + self.message)
                        self.acknowledge('retrying', self.message)
            if self.offline and now - getattr(self, 'offline_retry', 0.) > 10 and self.engage_since is None:
                self.offline_retry = now
                self.reconnect_offline()
            arms = self.bridge.mirror_step()
            if not arms and not any(self.side_answering(s) for s in self.bridge.sides):
                self.power_lost('arms stopped answering')
            return 1.0 / rb.MIRROR_HZ
        if self.mode == 'drive':
            faulted = [s for s in self.bridge.sides if s.arm.state is ArmState.FAULT]
            if not any(self.side_answering(s) for s in self.bridge.sides):
                self.power_lost('power lost during drive')
            elif faulted:
                why = '; '.join(f'{s.name}: {s.arm.fault_reason}' for s in faulted)
                healthy = [s for s in self.bridge.sides if s.arm.state is ArmState.ENABLED and s not in faulted]
                if not healthy:
                    self.release_all('fault in drive (' + why + ')')
                else:
                    with self.lock:
                        stuck = [s.name for s in faulted if not self.release_side(s)]
                    if stuck:  # the healthy arm returns and releases too; only the stuck arm holds
                        self.release_all('fault in drive (' + why + ')')
                    else:
                        self.end_test('aborted', f'fault: {why}')
                        self.set('drive', self.drive_message() + f' (other arm released after a fault: {why})')
            elif self.stop_requested or not fresh or not c.get('drive'):
                self.release_all('service stopping' if self.stop_requested else 'control heartbeat lost' if not fresh
                                 else 'Torque off pressed')
            else:
                self.drive_tick(now, c.get('test'))
                return 1.0 / DRIVE_HZ
            return 0.
        # hold / returning: the control thread handles Release now; detect power loss here
        if self.mode == 'hold':
            if not any(self.side_answering(s) for s in self.bridge.sides if s.name in self.holding):
                self.power_lost('holding arm lost power')
            else:
                self.publish_measured()
        return .1

    def run(self):
        threading.Thread(target=self.status_loop, daemon=True).start()
        threading.Thread(target=self.control_loop, daemon=True).start()
        try:
            while True:
                started = time.monotonic()
                with self.lock:
                    try:
                        period = self.step(started)
                    except Exception as error:  # e.g. a bus closed under us after a power loss
                        print(f'step failed: {error!r}', flush=True)
                        self.power_lost('arm communication failed')
                        period = RECONNECT_S
                if self.stop_requested and self.mode in ('mirror', 'off'):
                    break  # never exit with an arm holding: wait for Release now
                self.bridge.status()
                time.sleep(max(0.0, period - (time.monotonic() - started)))
        finally:
            for side in self.bridge.sides:
                try:
                    if side.arm.state not in ACTIVE:
                        side.arm.disconnect()
                except Exception:
                    pass
            self.exited = True
            print('service exited', flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--left')
    ap.add_argument('--right')
    ap.add_argument('--state-dir', default=str(rb.DEFAULT_DIR))
    ap.add_argument('--ue-state', help='shared ue_state.json (per-arm processes)')
    ap.add_argument('--control', help='shared control.json (per-arm processes)')
    ap.add_argument('--max-velocity', type=float, default=0.6)
    ap.add_argument('--gripper', action='store_true')
    args = ap.parse_args()
    if not (args.left or args.right):
        ap.error('pass --left and/or --right adapter serials')
    if not 0 < args.max_velocity <= 1.5:
        ap.error('--max-velocity must be in (0, 1.5] rad/s')
    service = Service(args)

    def stop(*_):
        service.stop_requested = True  # handled in the loop: release first, never exit holding
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    service.run()


if __name__ == '__main__':
    sys.exit(main())
