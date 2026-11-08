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
from maker_arm.arm import ArmState

DEADMAN_S = 2.0          # drive needs a control heartbeat at least this fresh
UE_LOST_S = 5.0          # drive needs Unreal state within this
POWER_LOST_S = 2.0       # no motor answered for this long -> arms are unpowered -> 'off'
RECONNECT_S = 3.0
PREFLIGHT_READS = 8
DRIVE_HZ = 25.0
ENGAGE_WINDOW_S = 10.0   # retry a refused engage (e.g. Table 10 still easing back after a preview)
ENGAGE_RETRY_S = 1.5
ACTIVE = (ArmState.ENABLED, ArmState.FAULT)
AT_ENGAGE_DEG = 2.0      # a faulted arm still this close to its engage pose is released (it is where
                         # the verified return would put it anyway)


class Service:
    def __init__(self, args):
        self.args = args
        rb.DRIVE_HZ = DRIVE_HZ  # Bridge.engage() reads the module value
        self.bridge = rb.Bridge(args)
        self.dir = Path(args.state_dir)
        self.control_path = self.dir / 'control.json'
        self.status_path = self.dir / 'service.json'
        self.control = {}
        self.mode, self.message, self.holding = 'off', 'starting', []
        self.release_seq = self.drive_seq = None
        self.engage_since, self.engage_last = None, 0.
        self.ue_seen = time.monotonic()
        self.lock = threading.RLock()
        self.stop_requested = False
        self.engage_pose = {}
        self.all_sides = list(self.bridge.sides)
        self.last_bus_log = 0.
        self.exited = False

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
            if self.release_seq is None:
                self.release_seq = seq
            elif seq != self.release_seq:
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
            out[side.name] = {'error_frames': getattr(backend, 'error_frames', None), 'echo_frames': getattr(backend, 'echo_frames', None),
                              'oldest_ms': None if oldest == float('inf') else int(1000 * oldest)}
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
        # Fresh Arm objects: a closed or faulted backend cannot simply reconnect.
        self.bridge.sides = [rb.Side(n, s) for n, s in (('left', self.args.left), ('right', self.args.right)) if s]
        self.all_sides = list(self.bridge.sides)
        for side in self.bridge.sides:
            side.arm.connect()
        self.bridge.mode = 'mirror'
        self.holding = []

    def power_lost(self, why):
        self.bridge.sides = list(self.all_sides)
        for side in self.bridge.sides:
            try:
                side.arm.disconnect()  # nothing is powered; just release the bus
            except Exception:
                pass
        self.bridge.mode, self.holding = 'mirror', []
        self.set('off', why + ': torque is off; reconnecting when the arms answer')

    def release_now(self, why):
        """Torque off immediately on every powered arm (SDK estop: safe from any thread)."""
        with self.lock:
            for side in self.bridge.sides:
                if side.arm.state in ACTIVE:
                    side.arm.estop()
            self.bridge.mode, self.holding = 'mirror', []
            self.bridge.sides = list(self.all_sides)
            self.set('mirror', why + ': torque off')

    def release_all(self, why):
        """Return each arm to its engage pose and release it; a failed return holds (never blocks)."""
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
            else:
                self.set('mirror', 'torque off')

    def try_engage(self):
        try:
            ok = self.bridge.engage()
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
        for _ in range(PREFLIGHT_READS):
            for side in self.bridge.sides:
                fresh = side.arm.refresh(wait=True, timeout=0.05)
                if not fresh or not all(fresh):
                    missing = [side.arm.config.joints[i].label for i, ok in enumerate(fresh or []) if not ok]
                    return f"{side.name} arm: {', '.join(missing) or 'motors'} not answering reliably (check power/cables)"
        return None

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
            try:
                self.connect()
                self.set('mirror', 'arms connected (torque off)')
            except Exception as error:
                self.set('off', 'arms not answering (power off, E-stop or cable?): torque is off; reconnecting')
                time.sleep(RECONNECT_S)
            return 0.
        if self.mode == 'mirror':
            if c.get('drive') and fresh and c.get('drive_seq') != self.drive_seq:
                self.drive_seq = c.get('drive_seq')
                self.engage_since, self.engage_last = now, 0.
                print(f"drive requested from {c.get('source', 'web')}", flush=True)
            if c.get('drive') and fresh and self.engage_since is not None and now - self.engage_last >= ENGAGE_RETRY_S:
                if now - self.engage_since > ENGAGE_WINDOW_S:
                    self.engage_since = None
                    self.set('mirror', 'drive not engaged: ' + self.message)
                else:
                    self.engage_last = now
                    wanted = c.get('sides') or ['left', 'right']
                    self.bridge.sides = [s for s in self.all_sides if s.name in wanted] or list(self.all_sides)
                    reason = self.preflight()
                    if reason:
                        self.bridge.sides = list(self.all_sides)
                        self.set('mirror', 'drive refused: ' + reason)
                    elif self.try_engage():
                        self.engage_since = None
                        self.set('drive', 'torque on: ' + ' and '.join(s.name for s in self.bridge.sides) + ' arm follows Table 10')
                        return 1.0 / DRIVE_HZ
                    else:
                        self.bridge.sides = list(self.all_sides)
                        self.set('mirror', 'drive refused: Unreal does not match the real arms within 5 deg yet')
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
                        names = ', '.join(s.name for s in healthy)
                        self.set('drive', f'torque on: {names} arm follows Table 10 (other arm released after a fault: {why})')
            elif self.stop_requested or not fresh or not c.get('drive') or now - self.ue_seen > UE_LOST_S:
                self.release_all('service stopping' if self.stop_requested else 'control heartbeat lost' if not fresh
                                 else 'Torque off pressed' if not c.get('drive') else 'Unreal state lost')
            else:
                self.bridge.drive_step()
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
                    period = self.step(started)
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
