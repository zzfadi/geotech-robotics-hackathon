#!/usr/bin/env python3
"""A single, bounded claw test. Read-only unless --move is explicitly supplied."""

import argparse
from dataclasses import replace
import json
import logging
import math
from pathlib import Path
import time

from arms import resolve
from gsusb_backend import GsUsbBackend
from maker_arm import protocol
from maker_arm.arm import Arm, ArmState
from maker_arm.config import ArmConfig
from maker_arm.profiles import DEFAULT_ARM_CONFIG

STEP = math.radians(5)
SPEED = math.radians(3)
ARRIVAL = math.radians(0.7)


def claw_config(full_open=False):
    base = ArmConfig.from_yaml(str(DEFAULT_ARM_CONFIG))
    joint = next(j for j in base.joints if j.name == "gripper")
    if joint.motor_id != 7 or joint.direction != 1 or joint.closing_direction != 1:
        raise ValueError("This test requires the standard motor-7 Maker Arm gripper")
    config = replace(
        base,
        joints=[replace(joint, kp=10.0 if full_open else 5.0,
                        kd=2.0 if full_open else 0.5,
                        hold_torque_nm=None if full_open else 0.1)],
        required_motor_ids=[7],
        control_rate_hz=25,
        max_velocity=SPEED,
        gravity_scale=0,
        hold_on_fault=False,
    )
    config._validate()
    return config


def plan_targets(start, joint):
    if not math.isfinite(start) or not math.radians(-12) <= start <= 0:
        raise ValueError("Claw is not near its expected closed pose; no motion authorized")
    opened = max(joint.lo, start - STEP)
    closed = min(joint.hi, start)
    if not joint.lo <= opened < closed <= joint.hi or closed - opened < math.radians(2):
        raise ValueError("Not enough safe travel for this small claw test")
    return opened, closed


def plan_open_target(start, joint):
    if not math.isfinite(start) or not joint.lo <= start <= 0:
        raise ValueError("Claw is outside its expected travel; refusing to open")
    return joint.lo


def check_feedback(arm, start, opened):
    motor = arm.motors[0]
    fb = motor.feedback
    if arm.state is not ArmState.ENABLED or arm.fault_reason:
        raise RuntimeError(f"Claw controller stopped: {arm.fault_reason or arm.state.name}")
    if fb is None or motor.feedback_age > 0.15:
        raise RuntimeError("Claw feedback is missing or stale")
    if not all(math.isfinite(x) for x in (fb.position, fb.velocity, fb.torque, fb.temperature)):
        raise RuntimeError("Claw feedback contains non-finite values")
    if motor.fault_latched or fb.warning_flag or fb.fault_flag:
        raise RuntimeError(f"Claw reported a warning or fault: {motor.fault_latched}")
    if motor.params_supported and fb.mode != protocol.MODE_RUN:
        raise RuntimeError(f"Claw left enabled mode: {fb.mode}")
    if not motor.params_supported and fb.mode in (protocol.MODE_CALIBRATION, 3):
        raise RuntimeError(f"Unexpected legacy claw mode: {fb.mode}")
    position = arm.get_joint_positions()[0]
    if not opened - math.radians(1.5) <= position <= start + math.radians(1.5):
        raise RuntimeError("Claw moved outside the small test envelope")
    if abs(fb.torque) > 0.65:
        raise RuntimeError(f"Claw effort {fb.torque:+.3f} Nm exceeded 0.65 Nm; stopping")
    if abs(fb.velocity) > math.radians(20):
        raise RuntimeError(f"Claw speed {math.degrees(fb.velocity):+.1f} deg/s exceeded 20 deg/s; stopping")
    if fb.temperature >= 45:
        raise RuntimeError(f"Claw is too warm: {fb.temperature:.1f} C")
    return position


def move_to(arm, goal, start, opened, samples, full_open=False):
    duration = abs(goal - start) / arm.config.max_velocity + 8 if full_open else 4
    deadline = time.monotonic() + duration
    tolerance = math.radians(2) if full_open else ARRIVAL
    settled_since = None
    last_sequence = -1
    anchor, last_progress = start, time.monotonic()
    while time.monotonic() < deadline:
        motor = arm.motors[0]
        position = arm.get_joint_positions()[0]
        if motor.feedback_sequence != last_sequence:
            last_sequence = motor.feedback_sequence
            samples.append({
                "position_deg": math.degrees(position),
                "velocity_deg_s": math.degrees(motor.feedback.velocity),
                "torque_nm": motor.feedback.torque,
                "temperature_c": motor.feedback.temperature,
            })
        position = check_feedback(arm, start, opened)
        if full_open and position < anchor - math.radians(0.25):
            anchor, last_progress = position, time.monotonic()
        if full_open and time.monotonic() - last_progress > 3 and abs(position - goal) > tolerance:
            raise RuntimeError("Claw stopped progressing; not increasing force")
        # Bound following error so a stalled claw never builds force toward the distant endpoint.
        target = max(goal, position - math.radians(2.5)) if full_open else goal
        if not arm.set_joint_targets([target]):
            raise RuntimeError("Claw target was rejected")
        if abs(position - goal) <= tolerance and abs(motor.feedback.velocity) < math.radians(5):
            if settled_since is None:
                settled_since = time.monotonic()
            if time.monotonic() - settled_since >= 0.3:
                return position
        else:
            settled_since = None
        time.sleep(0.02)
    raise RuntimeError(
        f"Claw stopped at {math.degrees(position):+.2f} deg, target {math.degrees(goal):+.2f} deg; "
        "not increasing force or retrying")


def run_probe(arm, move=False, operator_cutoff=False, report=None, open_only=False):
    result = {} if report is None else report
    result.update(motion_requested=move, motion_completed=False, stage="preflight")
    attempted_enable = False
    try:
        arm.connect()
        motor = arm.motors[0]
        if not motor.params_supported and not operator_cutoff:
            raise RuntimeError("Verified motor-side watchdog support is required; refusing motion")
        original_timeout = None
        if motor.params_supported:
            original_timeout = motor.read_param(protocol.ParamIndex.CAN_TIMEOUT, "u32")
        else:
            print("MANUAL-CUTOFF MODE: no verified motor watchdog. Operator must remain at power switch.",
                  flush=True)
        positions = []
        result["preflight_samples"] = []
        for _ in range(6):
            if arm.refresh() != [True]:
                raise RuntimeError("Claw feedback is unreliable before enabling")
            fb = motor.feedback
            result["preflight_samples"].append({
                "position_deg": math.degrees(fb.position),
                "velocity_deg_s": math.degrees(fb.velocity),
                "temperature_c": fb.temperature,
                "mode": fb.mode,
                "warning": fb.warning_flag,
                "fault": fb.fault_flag,
            })
            if (fb.mode != protocol.MODE_RESET or fb.warning_flag or fb.fault_flag
                    or not all(math.isfinite(x) for x in
                               (fb.position, fb.velocity, fb.torque, fb.temperature))
                    or abs(fb.velocity) > math.radians(5) or fb.temperature >= 45):
                raise RuntimeError(
                    "Claw must be disabled, still, cool and fault-free before the test: "
                    f"mode={fb.mode}, velocity={math.degrees(fb.velocity):+.2f} deg/s, "
                    f"temperature={fb.temperature:.1f} C, warning={fb.warning_flag}, fault={fb.fault_flag}")
            positions.append(arm.get_joint_positions()[0])
            time.sleep(0.03)
        if max(positions) - min(positions) > math.radians(0.5):
            raise RuntimeError("Claw is moving before enable; refusing motion")
        start = positions[-1]
        if open_only:
            opened = plan_open_target(start, arm.config.joints[0])
            closed = None
        else:
            opened, closed = plan_targets(start, arm.config.joints[0])
        result.update({
            "motion_requested": move,
            "motor_ids": [7],
            "start_deg": math.degrees(start),
            "open_target_deg": math.degrees(opened),
            "close_target_deg": None if open_only else math.degrees(closed),
            "speed_deg_s": math.degrees(arm.config.max_velocity),
            "open_only": open_only,
            "watchdog_ms": arm.config.joints[0].can_timeout_ms if motor.params_supported else None,
            "operator_cutoff": operator_cutoff,
            "original_watchdog_ticks": original_timeout,
        })
        print(json.dumps(result), flush=True)
        if not move:
            return result
        if open_only and abs(start - opened) <= math.radians(2):
            result.update(motion_completed=True, stage="already_open",
                          open_measured_deg=math.degrees(start))
            return result
        attempted_enable = True
        result["stage"] = "enable"
        arm.enable()
        samples = []
        result["samples"] = samples
        result["stage"] = "opening"
        result["open_measured_deg"] = math.degrees(
            move_to(arm, opened, start, opened, samples, full_open=open_only))
        if not open_only:
            result["stage"] = "closing"
            result["close_measured_deg"] = math.degrees(move_to(arm, closed, start, opened, samples))
        result.update(motion_completed=True, stage="completed")
        return result
    finally:
        try:
            if attempted_enable:
                result["release_command_acknowledged"] = False
                arm.disable()
                if arm.unconfirmed_disable:
                    raise RuntimeError("CLAW RELEASE UNCONFIRMED. Cut arm power immediately.")
                result["release_command_acknowledged"] = True
                result["release_mode_verified"] = bool(arm.motors[0].params_supported)
                if arm.motors[0].params_supported:
                    print("Claw motor release confirmed. No lifting joints were enabled.", flush=True)
                else:
                    print("Claw torque-off command acknowledged; legacy firmware cannot verify mode. "
                          "No lifting joints were enabled.", flush=True)
        finally:
            arm.disconnect()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arm", help="Saved name or USB adapter serial")
    parser.add_argument("--move", action="store_true", help="Requires an operator-ready confirmation")
    parser.add_argument("--operator-cutoff", action="store_true",
                        help="Explicit consent to supervised operation without a verified motor watchdog")
    parser.add_argument("--open-only", action="store_true",
                        help="Open to the vendor limit with bounded following error; do not close")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    name, serial = resolve(args.arm)
    config = claw_config(full_open=args.open_only)
    print(f"Selected {name}, adapter {serial}; motor 7 ONLY", flush=True)
    arm = Arm(config, GsUsbBackend(serial))
    result = {"arm": name, "serial": serial}
    try:
        run_probe(arm, move=args.move, operator_cutoff=args.operator_cutoff, report=result,
                  open_only=args.open_only)
    finally:
        if args.output:
            args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "samples"}), flush=True)


if __name__ == "__main__":
    main()
