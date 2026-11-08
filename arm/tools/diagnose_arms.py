"""Bounded SDK feedback report; never enables torque, writes zeros or changes limits.

The SDK queries faults before requesting state. Its legacy state-read frame can
clear a fault arising between those requests; this is not passive CAN capture.
Nominal position checks deliberately exclude the SDK's 20-degree wrap grace.
This report does not authorize motion or prove disabled mode/watchdog behavior.
"""

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from arms import load_names, make_arm
from gsusb_backend import list_adapters


def finite(value):
    return value if value is not None and math.isfinite(value) else None


def snapshot(arm, fresh):
    positions = arm.get_joint_positions()
    rows = []
    for j, m, position, replied in zip(arm.config.joints, arm.motors, positions, fresh):
        fb = m.feedback
        age = m.feedback_age
        valid = bool(replied and fb is not None and math.isfinite(position)
                     and math.isfinite(age) and age <= arm.config.feedback_timeout
                     and all(math.isfinite(v) for v in
                             (fb.position, fb.velocity, fb.torque, fb.temperature)))
        rows.append({
            "motor_id": j.motor_id, "joint": j.label, "fresh": valid,
            "feedback_age_s": finite(age), "sequence": m.feedback_sequence,
            "raw_motor_deg": finite(math.degrees(fb.position)) if fb else None,
            "sdk_joint_deg": finite(math.degrees(position)),
            "limits_deg": [math.degrees(j.lo), math.degrees(j.hi)],
            "within_nominal_limits": bool(valid and j.lo <= position <= j.hi),
            "velocity_deg_s": finite(math.degrees(fb.velocity)) if fb else None,
            "torque_nm": finite(fb.torque) if fb else None,
            "temperature_c": finite(fb.temperature) if fb else None,
            "fault_word": m.fault_status.word if m.fault_status else None,
            "fault_latched": m.fault_latched,
            "warning_flag": fb.warning_flag if fb else None,
            "fault_flag": fb.fault_flag if fb else None,
            "reported_mode": fb.mode if fb else None,
            "typed_parameters_supported": m.params_supported,
            "mode_telemetry_supported": m.params_supported is True,
            "watchdog_verified": False,
        })
    return {"received_at": datetime.now(timezone.utc).isoformat(), "joints": rows}


def diagnose(samples=5):
    names = load_names()
    adapters = list_adapters()
    found = {a["serial"] for a in adapters}
    report = {
        "when": datetime.now(timezone.utc).isoformat(),
        "motion_requested": False,
        "missing_expected_adapters": [{"serial": s, "name": n}
                                      for s, n in names.items() if s not in found],
        "arms": [],
    }
    for adapter in adapters:
        item = {**adapter, "name": names.get(adapter["serial"], "unmapped"), "samples": []}
        report["arms"].append(item)
        arm = make_arm(adapter["serial"])
        try:
            arm.connect()
            for index in range(samples):
                item["samples"].append(snapshot(arm, arm.refresh()))
                if index + 1 < samples:
                    time.sleep(0.1)
        except Exception as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            arm.disconnect()
    report["inspection_ok"] = bool(report["arms"] and not report["missing_expected_adapters"]
        and all(not a.get("error") and a["samples"] and all(
            row["fresh"] and row["within_nominal_limits"] and row["fault_word"] == 0
            and not row["fault_latched"] and not row["warning_flag"] and not row["fault_flag"]
            for sample in a["samples"] for row in sample["joints"])
            for a in report["arms"]))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.samples <= 100:
        parser.error("--samples must be between 1 and 100")
    report = diagnose(args.samples)
    payload = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload)
        print(json.dumps({"output": str(args.output), "inspection_ok": report["inspection_ok"],
                          "missing_expected_adapters": report["missing_expected_adapters"],
                          "arms": [{"name": a["name"], "error": a.get("error"),
                                    "outside_nominal_limits": [r["joint"] for r in
                                        (a["samples"][-1]["joints"] if a["samples"] else [])
                                        if not r["within_nominal_limits"]]} for a in report["arms"]]}))
    else:
        print(payload, end="")
    raise SystemExit(0 if report["inspection_ok"] else 1)


if __name__ == "__main__":
    main()
