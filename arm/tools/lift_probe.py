"""Supervised six-joint hold, bounded elbow lift, and verified return.

Gripper is excluded. Starts read-only unless --move --operator-cutoff are given.
An aborted powered trial stays alive holding; it never disconnects an elevated
arm automatically. This is a bench routine, not collision-aware Cartesian control.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time

from arms import resolve
from gsusb_backend import GsUsbBackend
from maker_arm.arm import Arm, ArmState
from maker_arm.config import ArmConfig
from maker_arm.profiles import DEFAULT_ARM_CONFIG
from maker_arm import protocol
from wrist_probe import check_stationary_window

EFFORT_NM = (1.5, 5., 6., 2.5, 1., 1.)
NAMES = ['shoulder_pan', 'shoulder_lift', 'elbow_flex',
         'wrist_flex', 'wrist_yaw', 'wrist_roll']


def lift_config():
    base = ArmConfig.from_yaml(str(DEFAULT_ARM_CONFIG))
    joints = base.joints[:6]
    if [j.name for j in joints] != NAMES or [j.motor_id for j in joints] != list(range(1, 7)):
        raise ValueError('Requires stock six-joint Maker Arm profile')
    cfg = replace(base, joints=[replace(j, max_temperature_c=45.,
                  enable_temperature_c=42., warning_is_fault=True) for j in joints],
                  required_motor_ids=list(range(1, 7)), control_rate_hz=25.,
                  max_velocity=math.radians(3), feedback_timeout=.25,
                  hold_on_fault=True, rest_speed=math.radians(3),
                  rest_timeout=25., rest_settle=math.radians(4))
    cfg._validate()
    return cfg


def make_targets(config, start, step_deg):
    if len(start) != 6 or not all(math.isfinite(p) for p in start):
        raise ValueError('Need six finite measured positions')
    if not math.isfinite(step_deg) or not 0 < step_deg <= 30:
        raise ValueError('Elbow step must be positive and at most 30 degrees')
    # Admit only the observed folded/rest shoulder discrepancy, not the SDK's
    # entire 20-degree wrapping grace. Never change zero or joint limits.
    for i, (j, p) in enumerate(zip(config.joints, start)):
        grace = math.radians(4) if i == 1 else 0.
        if not j.lo - grace <= p <= j.hi + grace:
            raise RuntimeError(f'{j.name}: start outside bench-admitted limits')
    rest = [max(j.lo, min(j.hi, p)) for j, p in zip(config.joints, start)]
    target = list(rest)
    target[2] += math.radians(step_deg)
    if target[2] > config.joints[2].hi - math.radians(5):
        raise RuntimeError('Insufficient elbow travel')
    return rest, target


def snapshot(arm, stage):
    return {'monotonic_s': time.monotonic(), 'stage': stage,
            'position_deg': [math.degrees(p) for p in arm.get_joint_positions()],
            'velocity_deg_s': [math.degrees(m.feedback.velocity) for m in arm.motors],
            'torque_nm': [m.feedback.torque for m in arm.motors],
            'temperature_c': [m.feedback.temperature for m in arm.motors],
            'feedback_age_s': [m.feedback_age for m in arm.motors]}


def camera_guard(directory):
    """Check locally written image liveness, not calibrated sensor exposure time."""
    if directory is None or not (directory / 'capture-ready.txt').is_file():
        raise RuntimeError('A running overview capture is required')
    frames = list(directory.glob('camera-*.jpg'))
    if not frames:
        raise RuntimeError('Overview has no saved frames')
    latest = max(frames, key=lambda p: p.stat().st_mtime_ns)
    age = time.time() - latest.stat().st_mtime
    if not -.25 <= age <= 1.:
        raise RuntimeError(f'Overview stopped producing fresh frames (age {age:.2f}s)')
    return {'image': str(latest), 'file_age_s': age,
            'timing_basis': 'local JPEG write time; exposure timing unvalidated'}


def validate(arm, start=None, target=None, enabled=False):
    expected = ArmState.ENABLED if enabled else ArmState.CONNECTED
    if arm.state is not expected or arm.fault_reason:
        raise RuntimeError(f'Controller state/fault: {arm.state.name} {arm.fault_reason}')
    pos = arm.get_joint_positions()
    for i, m in enumerate(arm.motors):
        fb = m.feedback
        if fb is None or not math.isfinite(m.feedback_age) or m.feedback_age > .25:
            raise RuntimeError(f'Motor {i+1}: stale feedback')
        if not all(math.isfinite(v) for v in (pos[i], fb.velocity, fb.torque, fb.temperature)):
            raise RuntimeError(f'Motor {i+1}: nonfinite feedback')
        if m.fault_latched or fb.warning_flag or fb.fault_flag:
            raise RuntimeError(f'Motor {i+1}: warning/fault')
        if m.params_supported and fb.mode != (protocol.MODE_RUN if enabled else protocol.MODE_RESET):
            raise RuntimeError(f'Motor {i+1}: unexpected mode')
        if not m.params_supported and fb.mode in (protocol.MODE_CALIBRATION, 3):
            raise RuntimeError(f'Motor {i+1}: unexpected legacy mode')
        if fb.temperature >= 42 or abs(fb.torque) > EFFORT_NM[i]:
            raise RuntimeError(f'Motor {i+1}: temperature/effort guard ({fb.temperature}, {fb.torque})')
        if abs(math.degrees(fb.velocity)) > 20:
            raise RuntimeError(f'Motor {i+1}: measured speed exceeds 20 deg/s')
        if start is not None:
            lo, hi = min(start[i], target[i]), max(start[i], target[i])
            if not lo - math.radians(4) <= pos[i] <= hi + math.radians(4):
                raise RuntimeError(f'Motor {i+1}: outside bounded motion envelope')
    return pos


def preflight(arm, report, step_deg):
    readings = []
    for _ in range(21):
        if arm.refresh() != [True] * 6:
            raise RuntimeError('Incomplete six-joint feedback')
        validate(arm)
        readings.append(snapshot(arm, 'preflight'))
        time.sleep(.05)
    report['preflight_samples'] = readings
    report['stationary_evidence'] = [check_stationary_window([
        {'monotonic_s': s['monotonic_s'], 'position_deg': s['position_deg'][i],
         'velocity_deg_s': s['velocity_deg_s'][i]} for s in readings],
        require_velocity_agreement=False) for i in range(6)]
    start = arm.get_joint_positions()
    rest, target = make_targets(arm.config, start, step_deg)
    report.update(start_deg=list(map(math.degrees, start)),
                  clipped_rest_target_deg=list(map(math.degrees, rest)),
                  lift_target_deg=list(map(math.degrees, target)))
    return start, rest, target


def segment(arm, goal, start, envelope, report, duration=25., camera_directory=None):
    deadline = time.monotonic() + duration
    settled = None
    while time.monotonic() < deadline:
        pos = validate(arm, start, envelope, enabled=True)
        if camera_directory is not None:
            report['latest_camera'] = camera_guard(camera_directory)
        report['samples'].append(snapshot(arm, report['stage']))
        # Bound positional demand even if a joint is obstructed or stalls.
        lead = math.radians(2)
        demand = [min(p+lead, max(p-lead, g)) for p, g in zip(pos, goal)]
        if not arm.set_joint_targets(demand):
            raise RuntimeError('Target rejected')
        if max(abs(p-g) for p, g in zip(pos, goal)) <= math.radians(2):
            settled = time.monotonic() if settled is None else settled
            if time.monotonic() - settled >= .6:
                return list(map(math.degrees, pos))
        else:
            settled = None
        time.sleep(.04)
    raise RuntimeError('Segment did not converge; holding for inspection, no automatic retry')


def save(report, path):
    # Losing disk space or receiving bad diagnostic data must not terminate a
    # controller that is holding an elevated arm.
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: finite(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [finite(item) for item in value]
        return value
    try:
        path.write_text(json.dumps(finite(report), indent=2, allow_nan=False) + '\n')
        return True
    except Exception as exc:
        print(f'EVIDENCE_WRITE_FAILED: {exc}; controller lifecycle unchanged', flush=True)
        return False


def finish(arm, report):
    """The SDK must verify rest before release; False means the caller stays alive."""
    # enable() itself releases after a partial startup failure; stop() returns
    # True in CONNECTED without a physical return. Do not label that a lift return.
    was_powered = arm.state in (ArmState.ENABLED, ArmState.FAULT)
    report['stage'] = 'verified_return'
    stopped = bool(arm.stop())
    report['return_verified'] = stopped if was_powered else None
    report['stop_from_state'] = 'powered' if was_powered else 'not_enabled'
    report['after_stop_deg'] = list(map(math.degrees, arm.get_joint_positions()))
    if not stopped:
        return False
    report['release_command_acknowledged'] = not bool(arm.unconfirmed_disable)
    report['release_mode_verified'] = all(m.params_supported for m in arm.motors)
    if not report['release_command_acknowledged']:
        return False
    arm.disconnect()
    return True


def keep_holding(arm, report, path):
    try:
        holding = arm.hold_current_position()
    except Exception as exc:
        holding = False
        report['hold_request_error'] = str(exc)
    report['stage'] = 'holding_for_inspection' if holding else 'startup_failed_inspection'
    report['controller_state_at_interruption'] = arm.state.name
    save(report, path)
    print(f'INSPECTION_REQUIRED: state={arm.state.name}, hold_requested={holding}. '
          'Controller stays alive. RETURN uses the SDK stop path; '
          'SUPPORTED_RELEASE is only for an operator-supported arm.', flush=True)
    # EOF and Ctrl-C never drop an elevated arm. A caller must retain this process.
    while True:
        try:
            command = sys.stdin.readline().strip()
            if command == 'RETURN':
                if finish(arm, report):
                    save(report, path)
                    return
                arm.hold_current_position()
                print('RETURN_UNVERIFIED: still holding; operator inspection required', flush=True)
            elif command == 'SUPPORTED_RELEASE':
                arm.disable()
                report['operator_supported_release'] = True
                report['release_command_acknowledged'] = not bool(arm.unconfirmed_disable)
                if report['release_command_acknowledged']:
                    arm.disconnect()
                    save(report, path)
                    return
                print('RELEASE_UNCONFIRMED: operator power cutoff required', flush=True)
            else:
                time.sleep(.2)
        except BaseException as exc:
            report['holding_error'] = f'{type(exc).__name__}: {exc}'
            print(report['holding_error'], flush=True)
            time.sleep(.2)
        save(report, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('arm')
    parser.add_argument('--move', action='store_true')
    parser.add_argument('--operator-cutoff', action='store_true')
    parser.add_argument('--step-deg', type=float, default=15.)
    parser.add_argument('--camera-directory', type=Path,
                        help='Running overview burst; required for powered motion, fresh JPEG every second')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.move and (not args.operator_cutoff or not sys.stdin.isatty()):
        parser.error('Powered trial requires --operator-cutoff and an interactive PTY for hold/recovery')
    if args.move and args.camera_directory is None:
        parser.error('Powered trial requires --camera-directory with a running overview capture')
    if args.output.exists():
        parser.error('Use a new output file')
    cfg = lift_config()
    if not math.isfinite(args.step_deg) or not 0 < args.step_deg <= 30:
        parser.error('Step must be finite, positive, at most 30 degrees')
    name, serial = resolve(args.arm)
    report = {'when': datetime.now(timezone.utc).isoformat(), 'arm': name, 'serial': serial,
              'motor_ids': list(range(1, 7)), 'gripper_enabled': False,
              'step_deg': args.step_deg, 'speed_deg_s': 3., 'effort_guards_nm': EFFORT_NM,
              'motion_requested': args.move, 'motion_completed': False, 'samples': [],
              'watchdog_verified': False}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    arm = Arm(cfg, GsUsbBackend(serial))
    powered_attempt = False
    try:
        arm.connect()
        start, rest, target = preflight(arm, report, args.step_deg)
        if not save(report, args.output):
            raise RuntimeError('Cannot persist preflight evidence; refusing enable')
        if args.move:
            report['latest_camera'] = camera_guard(args.camera_directory)
            powered_attempt = True
            report['stage'] = 'enable'
            arm.enable()
            report['stage'] = 'lift'
            print(f'LIFT: elbow +{args.step_deg:g} degrees; gripper excluded', flush=True)
            report['outbound_measured_deg'] = segment(arm, target, start, target, report,
                                                      camera_directory=args.camera_directory)
            report['stage'] = 'lower'
            print('LOWER: returning to measured rest', flush=True)
            report['return_measured_deg'] = segment(arm, rest, start, target, report,
                                                    camera_directory=args.camera_directory)
            if not finish(arm, report):
                raise RuntimeError('Rest or release unverified')
            report.update(stage='completed', motion_completed=True)
        else:
            report['stage'] = 'inspection_only'
            arm.disconnect()
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        print(report['error'], flush=True)
        if powered_attempt:
            keep_holding(arm, report, args.output)
        else:
            arm.disconnect()
    finally:
        save(report, args.output)
        print(json.dumps({k:v for k,v in report.items() if k not in ('samples','preflight_samples')}), flush=True)
    return 1 if 'error' in report else 0


if __name__ == '__main__':
    sys.exit(main())
