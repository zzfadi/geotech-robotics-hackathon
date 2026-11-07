#!/usr/bin/env python3
"""Read-only live view of every connected arm. Never enables or releases torque.

With --seconds N it stops after N seconds and reports observed position changes.
Do not move joints by hand merely to identify an adapter.
"""

import argparse
import math
import sys
import time

from arms import adapters_by_name, joint_labels, make_arm


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seconds", type=float, help="stop after this many seconds and report motion")
    ap.add_argument("--plain", action="store_true", help="append lines instead of redrawing")
    a = ap.parse_args()
    if a.seconds is not None and (not math.isfinite(a.seconds) or a.seconds <= 0):
        ap.error("--seconds must be finite and positive")

    labels = joint_labels()
    short = {"shoulder_pan": "pan", "shoulder_lift": "lift", "elbow_flex": "elbow", "wrist_flex": "wflex",
             "wrist_yaw": "wyaw", "wrist_roll": "wroll", "gripper": "grip"}
    arms = {}
    for name, serial in adapters_by_name().items():
        arm = make_arm(serial)
        try:
            arm.connect()
        except Exception as e:
            print(f"{name}: could not connect: {e}", file=sys.stderr)
            arm.disconnect()
            continue
        arms[name] = arm
    if not arms:
        sys.exit("no arms connected")

    start = {n: arm.get_joint_positions() for n, arm in arms.items()}
    peak = {n: [0.0] * len(labels) for n in arms}
    end = time.monotonic() + a.seconds if a.seconds else None
    try:
        while end is None or time.monotonic() < end:
            pos, stale = {}, []
            for n, arm in arms.items():
                ok = arm.refresh()
                pos[n] = arm.get_joint_positions()
                for i, p in enumerate(pos[n]):
                    peak[n][i] = max(peak[n][i], abs(p - start[n][i]))
                if not all(ok):
                    stale.append(f"{n}: no reply from {[i+1 for i, f in enumerate(ok) if not f]}")
            lines = ["joint  " + "".join(f"{n:>13}" for n in arms)]
            for i, label in enumerate(labels):
                row = f"{short.get(label, label):<6} "
                for n in arms:
                    mark = "*" if abs(pos[n][i] - start[n][i]) > math.radians(3) else " "
                    row += f"{math.degrees(pos[n][i]):+12.1f}{mark}"
                lines.append(row)
            lines += stale
            if a.plain:
                print("\n".join(lines), flush=True)
            else:
                sys.stdout.write("\033[H\033[J" + "Live angles (deg), * = moved >3 deg\nRead-only. Ctrl-C to quit.\n\n"
                                 + "\n".join(lines) + "\n")
                sys.stdout.flush()
            time.sleep(0.1)
    except KeyboardInterrupt:
        pass
    finally:
        for arm in arms.values():
            arm.disconnect()

    print("\nlargest movement per arm:")
    for n in arms:
        i = max(range(len(labels)), key=lambda k: peak[n][k])
        print(f"  {n}: {math.degrees(peak[n][i]):.1f} deg on {labels[i]}")


if __name__ == "__main__":
    main()
