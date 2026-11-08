"""Supervised bounded motor-6 roll and return (2 degrees by default). No motion without --move.

Only motor 6 is configured. Never enables lifting joints or the gripper. This
is a bench diagnostic, not a calibrated Cartesian or whole-arm controller.
"""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from arms import resolve
from gsusb_backend import GsUsbBackend
from maker_arm.arm import Arm, ArmState
from maker_arm.config import ArmConfig
from maker_arm.profiles import DEFAULT_ARM_CONFIG
from maker_arm import protocol

STEP = math.radians(2)


def wrist_config(vendor_gains=False, speed_deg_s=1.):
    if not math.isfinite(speed_deg_s) or not 1 <= speed_deg_s <= 5:
        raise ValueError("Command speed must be finite and between 1 and 5 deg/s")
    base = ArmConfig.from_yaml(str(DEFAULT_ARM_CONFIG))
    joint = next(j for j in base.joints if j.name == 'wrist_roll')
    if joint.motor_id != 6 or joint.direction != 1:
        raise ValueError('Requires the stock motor-6 wrist-roll profile')
    config = replace(base, joints=[joint if vendor_gains else replace(joint, kp=5., kd=1.)],
                     required_motor_ids=[6], control_rate_hz=25., max_velocity=math.radians(speed_deg_s),
                     full_turn_grace=0., feedback_timeout=.15,
                     hold_on_fault=False, gravity_scale=0.)
    config._validate()
    return config


def validate(arm, start, enabled, step=STEP):
    m = arm.motors[0]
    fb = m.feedback
    expected = ArmState.ENABLED if enabled else ArmState.CONNECTED
    if arm.state is not expected or arm.fault_reason:
        raise RuntimeError(f'Controller state/fault: {arm.state.name} {arm.fault_reason}')
    if fb is None or not math.isfinite(m.feedback_age) or m.feedback_age > .15:
        raise RuntimeError('Missing or stale feedback')
    if not all(math.isfinite(v) for v in (fb.position, fb.velocity, fb.torque, fb.temperature)):
        raise RuntimeError('Nonfinite feedback')
    if m.fault_latched or fb.warning_flag or fb.fault_flag:
        raise RuntimeError('Motor warning or fault')
    if m.params_supported and fb.mode != (protocol.MODE_RUN if enabled else protocol.MODE_RESET):
        raise RuntimeError('Unexpected motor mode')
    if not m.params_supported and fb.mode in (protocol.MODE_CALIBRATION, 3):
        raise RuntimeError('Unexpected legacy motor mode')
    if fb.temperature >= 45 or abs(fb.torque) > .4:
        raise RuntimeError('Temperature or 0.4 Nm effort guard exceeded')
    speed_limit = 15 if enabled else 5
    if abs(math.degrees(fb.velocity)) > speed_limit:
        raise RuntimeError(f'Measured speed {math.degrees(fb.velocity):+.3f} exceeded {speed_limit} deg/s')
    p = arm.get_joint_positions()[0]
    j = arm.config.joints[0]
    if not math.isfinite(p) or not j.lo <= p <= j.hi:
        raise RuntimeError('Position outside nominal joint limits')
    if start is not None and not start - math.radians(1) <= p <= start + step + math.radians(1):
        raise RuntimeError('Position outside the bounded test envelope')
    return p


def segment(arm, target, start, report):
    step = math.radians(report.get("step_deg", 2.))
    duration = max(8., 2 * step / arm.config.max_velocity + 4.)
    end = time.monotonic() + duration
    settled = None
    sequence = -1
    while time.monotonic() < end:
        p = validate(arm, start, True, step)
        m = arm.motors[0]
        if sequence != m.feedback_sequence:
            sequence = m.feedback_sequence
            report['samples'].append({'elapsed_s': time.monotonic() - report['_began'],
                'stage': report['stage'], 'position_deg': math.degrees(p),
                'velocity_deg_s': math.degrees(m.feedback.velocity),
                'torque_nm': m.feedback.torque, 'temperature_c': m.feedback.temperature,
                'feedback_age_s': m.feedback_age})
        # A stalled joint cannot accumulate a distant position demand.
        bounded = min(p + math.radians(.5), max(p - math.radians(.5), target))
        if not arm.set_joint_targets([bounded]):
            raise RuntimeError('Target rejected')
        if abs(p - target) <= math.radians(.5) and abs(m.feedback.velocity) < math.radians(3):
            settled = time.monotonic() if settled is None else settled
            if time.monotonic() - settled >= .4:
                return math.degrees(p)
        else:
            settled = None
        time.sleep(.02)
    raise RuntimeError(f'Target not settled within {duration:.1f} seconds; no automatic retry')


def run(arm, report, move=False, operator_cutoff=False, step_deg=2.):
    if not math.isfinite(step_deg) or not 0 < step_deg <= 10:
        raise ValueError("Step must be finite and at most 10 degrees")
    step = math.radians(step_deg)
    attempted = False
    report.update(motion_requested=move, motion_completed=False, motor_ids=[6],
                  stage='preflight', samples=[], preflight_samples=[], operator_cutoff=operator_cutoff,
                  watchdog_verified=False, kp=arm.config.joints[0].kp,
                  kd=arm.config.joints[0].kd, following_error_cap_deg=.5, command_speed_deg_s=math.degrees(arm.config.max_velocity),
                  step_deg=step_deg, effort_guard_nm=.4, measured_speed_guard_deg_s=15.)
    try:
        arm.connect()
        positions = []
        for _ in range(10):
            if arm.refresh() != [True]:
                raise RuntimeError('Unreliable feedback before enable')
            fb = arm.motors[0].feedback
            sample = {
                'position_deg': math.degrees(arm.get_joint_positions()[0]),
                'velocity_deg_s': math.degrees(fb.velocity), 'torque_nm': fb.torque,
                'temperature_c': fb.temperature, 'mode': fb.mode}
            report['preflight_samples'].append({
                key: value if math.isfinite(value) else None for key, value in sample.items()})
            positions.append(validate(arm, None, False))
            time.sleep(.05)
        if max(positions)-min(positions) > math.radians(.5):
            raise RuntimeError('Wrist moved during preflight')
        start = positions[-1]
        j = arm.config.joints[0]
        if not j.lo + math.radians(5) < start < j.hi - step - math.radians(1):
            raise RuntimeError('Insufficient nominal joint travel')
        report.update(start_deg=math.degrees(start), target_deg=math.degrees(start+step),
                      typed_parameters_supported=arm.motors[0].params_supported)
        if not move:
            report['stage'] = 'inspection_only'
            return
        if not operator_cutoff:
            raise RuntimeError('Motor 6 has no configured watchdog; operator cutoff is required')
        attempted = True
        report['stage'] = 'enable'
        report['_began'] = time.monotonic()
        arm.enable()
        report['stage'] = 'outbound'
        report['outbound_measured_deg'] = segment(arm, start+step, start, report)
        report['stage'] = 'return'
        report['return_measured_deg'] = segment(arm, start, start, report)
        report.update(stage='completed', motion_completed=True)
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        try:
            if attempted:
                report['release_command_acknowledged'] = False
                arm.disable()
                if arm.unconfirmed_disable:
                    raise RuntimeError('WRIST RELEASE UNCONFIRMED: operator must cut power')
                report['release_command_acknowledged'] = True
                report['release_mode_verified'] = arm.motors[0].params_supported is True
        finally:
            arm.disconnect()
            report.pop('_began', None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('arm', help='Exact serial or saved rig label')
    parser.add_argument('--move', action='store_true')
    parser.add_argument('--operator-cutoff', action='store_true')
    parser.add_argument('--vendor-gains', action='store_true',
                        help='Use pinned SDK wrist gains (30/2), with unchanged speed/effort guards')
    parser.add_argument('--step-deg', type=float, default=2., help='Maximum 10 degrees; staged verification required')
    parser.add_argument('--speed-deg-s', type=float, default=1., help='Between 1 and 5 degrees/s')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists; choose a new evidence file')
    if not math.isfinite(args.step_deg) or not 0 < args.step_deg <= 10:
        parser.error("--step-deg must be finite, positive and at most 10")
    config = wrist_config(args.vendor_gains, args.speed_deg_s)
    name, serial = resolve(args.arm)
    report = {'when': datetime.now(timezone.utc).isoformat(), 'arm': name, 'serial': serial}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    arm = Arm(config, GsUsbBackend(serial))
    try:
        run(arm, report, args.move, args.operator_cutoff, args.step_deg)
    finally:
        args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+'\n')
        print(json.dumps({k:v for k,v in report.items() if k != 'samples'}), flush=True)


if __name__ == '__main__':
    main()
