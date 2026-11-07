#!/usr/bin/env python3
"""Read-only scan of every candleLight adapter: fault query + position per motor. Never enables torque."""

import argparse
import math
import sys

from gsusb_backend import GsUsbBackend, list_adapters
from maker_arm import protocol
from maker_arm.config import ArmConfig
from maker_arm.errors import ParamTimeout
from maker_arm.motor import Motor
from maker_arm.profiles import DEFAULT_ARM_CONFIG


def scan(serial: str, config: ArmConfig, max_id: int = 7) -> None:
    joints = {j.motor_id: j for j in config.joints}
    be = GsUsbBackend(serial)
    motors = {mid: Motor(mid, be, protocol.MOTOR_PARAMS[joints[mid].model] if mid in joints else protocol.RS00)
              for mid in range(1, max_id + 1)}

    def receive(can_id, data):
        m = motors.get(protocol.reply_motor_id(can_id, data))
        if m:
            m.handle_frame(protocol.parse_frame(can_id, data, m.params, config.host_id))

    be.set_recv_callback(receive)
    be.open()
    rows = []
    try:
        for mid, m in motors.items():
            try:
                status = m.query_fault(timeout=0.1, attempts=2)
            except ParamTimeout:
                continue
            fb = None
            if not status.has_fault:
                try:
                    fb = m.read_state(timeout=0.05, attempts=3)
                except ParamTimeout:
                    pass
            rows.append((mid, status, fb))
    finally:
        be.close()

    if not rows:
        print("  no motors answered (power on? 1 Mbps? MIT protocol?)")
        return
    print("   ID joint          position_deg  temp_C  status")
    for mid, status, fb in rows:
        j = joints.get(mid)
        name = j.label if j else "(not in profile)"
        pos = f"{math.degrees(fb.position):+12.1f}" if fb else "-".rjust(12)
        temp = f"{fb.temperature:6.1f}" if fb else "-".rjust(6)
        text = f"FAULT 0x{status.word:08X}: {status.text}" if status.has_fault else "ok"
        if j and fb and not (j.lo - config.full_turn_grace <= fb.position <= j.hi + config.full_turn_grace):
            text += f"  (outside limits [{math.degrees(j.lo):+.0f},{math.degrees(j.hi):+.0f}])"
        print(f"  {mid:3} {name:<14} {pos}  {temp}  {text}")
    missing = [mid for mid in joints if mid not in {r[0] for r in rows}]
    if missing:
        print(f"  missing: {missing}")


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    config = ArmConfig.from_yaml(str(DEFAULT_ARM_CONFIG))
    adapters = list_adapters()
    if not adapters:
        sys.exit("no gs_usb adapters found")
    failed = False
    for a in adapters:
        print(f"\nadapter {a['serial']} (usb bus {a['bus']})")
        try:
            scan(a["serial"], config)
        except Exception as e:
            print(f"  error: {e!r}", file=sys.stderr)
            failed = True
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
