#!/usr/bin/env python3
"""Real Maker Arms <-> Unreal Table 10, MakerMods SDK side. Run in an interactive terminal:

  ../../../maker-arm-sdk/.venv/bin/python Tools/real_arm_bridge.py --left SERIAL --right SERIAL

Needs the maker-arm-sdk clone with the local gs_usb backend (candleLight adapters on macOS), and
Tools/maker_arm_real_link.py running in the editor. They share Saved/RealArmLink/*.json.

Commands (type, then ENTER):
  m  mirror  torque OFF. Move a real arm by hand; Table 10 follows 1:1. Starts here.
  d  drive   torque ON. Drag a Table 10 gripper in Unreal; the real arm follows.
             Only engages when Unreal already matches the real pose (within 5 deg per joint),
             so the arm never jumps. Switch from mirror, where Unreal is posed from the real arm.
  q  quit    from drive: return to the pose at engage at 30 deg/s, then release torque.

Safety, on top of the SDK's own (limit clipping, startup sync, latched faults, verified
return-to-rest before release, gravity feed-forward):
- Joint speed is capped (--max-velocity, default 0.6 rad/s).
- Unreal state older than 0.3 s is ignored; the arm holds its last target.
- A target jumping more than 25 deg from the previous one is rejected and the arm holds.
- The gripper holds its engage position unless --gripper is passed (gap mapping is linear and
  unmeasured between closed and open).
- Joints 1-6 have no motor watchdog: if this process dies they hold their last command. Keep
  a hand on the hardware E-stop whenever drive is on.
"""

import argparse
import json
import math
import queue
import sys
import threading
import time
from pathlib import Path

from maker_arm.arm import Arm, ArmState
from maker_arm.cli.safety import require_interactive_terminal
from maker_arm.profiles import DEFAULT_ARM_CONFIG

import maker_arm_real_mapping as mapping

DEFAULT_DIR = Path(__file__).resolve().parents[1] / "Saved" / "RealArmLink"
UE_FRESH_S = 0.3
ENGAGE_TOLERANCE_DEG = 5.0
JUMP_DEG = 25.0
MIRROR_HZ = 30.0
DRIVE_HZ = 50.0


def write(path, value):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value))
    tmp.replace(path)


def deg(values):
    return [round(math.degrees(v), 1) for v in values]


class Side:
    def __init__(self, name, serial):
        self.name = name
        self.serial = serial
        self.arm = Arm.from_yaml(str(DEFAULT_ARM_CONFIG), backend="gs_usb", serial=serial)
        self.last_target = None
        self.gripper_hold = None
        self.warned = set()

    def warn_once(self, key, text):
        if key not in self.warned:
            self.warned.add(key)
            print(f"[{self.name}] {text}")


class Bridge:
    def __init__(self, args):
        self.args = args
        self.dir = Path(args.state_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.real_path = self.dir / "real_state.json"
        self.ue_path = self.dir / "ue_state.json"
        self.sides = [Side(n, s) for n, s in (("left", args.left), ("right", args.right)) if s]
        self.mode = "mirror"
        self.seq = 0
        self.lines = queue.Queue()
        self.last_status = 0.0
        self.last_ue_seq = None

    # ---- io -----------------------------------------------------------------------------
    def read_ue(self):
        try:
            ue = json.loads(self.ue_path.read_text())
        except (FileNotFoundError, ValueError, OSError):
            return None
        return ue if time.time() - ue.get("unix", 0) <= UE_FRESH_S else None

    def publish(self, arms):
        self.seq += 1
        write(self.real_path, {"seq": self.seq, "unix": time.time(), "mode": self.mode, "arms": arms})

    def measured(self, side):
        q = side.arm.get_joint_positions()
        if not all(math.isfinite(v) for v in q):
            return None
        return {"q": mapping.motor_to_urdf(q[:6]), "gap_m": mapping.gripper_to_gap(q[6]),
                "motor_deg": deg(q)}

    def _stdin(self):
        for line in sys.stdin:
            self.lines.put(line.strip().lower())
        self.lines.put("q")

    # ---- modes --------------------------------------------------------------------------
    def mirror_step(self):
        arms = {}
        for side in self.sides:
            fresh = side.arm.refresh(wait=True, timeout=0.02)
            if fresh and all(fresh):
                m = self.measured(side)
                if m:
                    arms[side.name] = m
            else:
                side.warn_once("stale", "some motors did not answer this cycle (holding last Unreal pose)")
        self.publish(arms)
        return arms

    def engage(self):
        ue = self.read_ue()
        if ue is None:
            print("drive refused: no fresh Unreal state -- is maker_arm_real_link running in the editor?")
            return False
        for side in self.sides:
            side.arm.refresh(wait=True, timeout=0.05)
            meas = side.arm.get_joint_positions()
            arm = ue.get("arms", {}).get(side.name)
            if not arm or not arm.get("q"):
                print(f"drive refused: Unreal has no {side.name} arm state")
                return False
            target = mapping.urdf_to_motor(arm["q"])
            diffs = [abs(math.degrees(t - m)) for t, m in zip(target, meas[:6])]
            worst = max(range(6), key=lambda i: diffs[i])
            if diffs[worst] > ENGAGE_TOLERANCE_DEG:
                print(f"drive refused: Unreal {side.name} {mapping.JOINTS[worst]} differs from the real arm "
                      f"by {diffs[worst]:.1f} deg (limit {ENGAGE_TOLERANCE_DEG}). Stay in mirror (m) until "
                      "Unreal matches, and don't drag the gripper before engaging.")
                return False
        for side in self.sides:
            cfg = side.arm.config
            cfg.control_rate_hz = DRIVE_HZ
            cfg.max_velocity = self.args.max_velocity
            side.arm.enable()
            meas = side.arm.get_joint_positions()
            side.gripper_hold = meas[6]
            side.last_target = list(meas)
            side.arm.set_joint_targets(side.last_target)
            side.warned.clear()
        self.mode = "drive"
        self.last_ue_seq = None
        print(f"DRIVE ON ({', '.join(s.name for s in self.sides)}): drag the Table 10 grippers in Unreal. "
              "Type m or q to return to rest and release.")
        return True

    def drive_step(self):
        ue = self.read_ue()
        arms = {}
        if ue is None:
            for side in self.sides:
                side.warn_once("ue-stale", "Unreal state stale -- holding last target")
        elif ue.get("seq") != self.last_ue_seq:
            self.last_ue_seq = ue.get("seq")
            for side in self.sides:
                side.warned.discard("ue-stale")
                arm = ue.get("arms", {}).get(side.name)
                if not arm or not arm.get("q") or side.arm.state is not ArmState.ENABLED:
                    continue
                target = mapping.urdf_to_motor(arm["q"])
                target.append(mapping.gap_to_gripper(arm.get("gap_m", 0.0)) if self.args.gripper
                              else side.gripper_hold)
                jump = max(abs(math.degrees(a - b)) for a, b in zip(target, side.last_target))
                if jump > JUMP_DEG:
                    side.warn_once("jump", f"rejected a {jump:.0f} deg target jump -- holding. "
                                           "Return to mirror (m) to resync.")
                    continue
                side.warned.discard("jump")
                if side.arm.set_joint_targets(target):
                    side.last_target = target
        for side in self.sides:
            if side.arm.state is ArmState.FAULT:
                side.warn_once("fault", f"FAULT: {side.arm.fault_reason} -- the SDK is holding its pose. "
                                        "Type m/q to return to rest and release.")
            m = self.measured(side)
            if m:
                arms[side.name] = m
        self.publish(arms)

    def release(self):
        """Verified return to the engage pose, then torque off (SDK stop path)."""
        for side in self.sides:
            if side.arm.state not in (ArmState.ENABLED, ArmState.FAULT):
                continue
            print(f"[{side.name}] returning to the engage pose...")
            if side.arm.stop():
                print(f"[{side.name}] rest reached; torque released")
                continue
            print(f"[{side.name}] could not verify the return; the arm is HOLDING. Support it, then type RELEASE.")
            while True:
                line = self.lines.get()
                if line == "release":
                    side.arm.disable()
                    print(f"[{side.name}] torque released")
                    break
                print("torque remains on; type RELEASE when the arm is supported")
        self.mode = "mirror"

    # ---- main ---------------------------------------------------------------------------
    def status(self):
        now = time.monotonic()
        if now - self.last_status < 2.0:
            return
        self.last_status = now
        ue = self.read_ue()
        parts = []
        for side in self.sides:
            q = side.arm.get_joint_positions()
            text = f"{side.name} real {deg(mapping.motor_to_urdf(q[:6]))}"
            arm = (ue or {}).get("arms", {}).get(side.name)
            if arm and arm.get("q") and all(math.isfinite(v) for v in q[:6]):
                err = max(abs(math.degrees(a - b)) for a, b in zip(mapping.urdf_to_motor(arm["q"]), q[:6]))
                text += f" | unreal-real max {err:.1f} deg"
            parts.append(text)
        print(f"[{self.mode}] " + "  ".join(parts) + ("" if ue else "  (no fresh Unreal state)"))

    def run(self):
        for side in self.sides:
            side.arm.connect()
            print(f"[{side.name}] connected on adapter {side.serial}: "
                  f"motor deg {deg(side.arm.get_joint_positions())}")
        threading.Thread(target=self._stdin, daemon=True).start()
        print(__doc__.split("Commands")[1].split("Safety")[0].strip())
        print("MIRROR (torque off): move a real arm by hand and watch Table 10.")
        try:
            while True:
                started = time.monotonic()
                try:
                    line = self.lines.get_nowait()
                except queue.Empty:
                    line = None
                if line == "q":
                    break
                if line == "d" and self.mode == "mirror":
                    self.engage()
                elif line == "m" and self.mode == "drive":
                    self.release()
                    print("MIRROR (torque off)")
                elif line:
                    print("commands: m (mirror), d (drive), q (quit)")
                if self.mode == "mirror":
                    self.mirror_step()
                    period = 1.0 / MIRROR_HZ
                else:
                    self.drive_step()
                    period = 1.0 / DRIVE_HZ
                self.status()
                time.sleep(max(0.0, period - (time.monotonic() - started)))
        finally:
            self.release()
            for side in self.sides:
                side.arm.disconnect()
            print("bridge exited; torque off")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--left", help="gs_usb adapter serial of the arm Unreal calls 'left'")
    ap.add_argument("--right", help="gs_usb adapter serial of the arm Unreal calls 'right'")
    ap.add_argument("--state-dir", default=str(DEFAULT_DIR))
    ap.add_argument("--max-velocity", type=float, default=0.6, help="joint speed cap in drive, rad/s (<= 1.5)")
    ap.add_argument("--gripper", action="store_true", help="also follow the Unreal jaw gap in drive")
    a = ap.parse_args()
    if not (a.left or a.right):
        ap.error("pass --left and/or --right adapter serials")
    if not 0 < a.max_velocity <= 1.5:
        ap.error("--max-velocity must be in (0, 1.5] rad/s")
    require_interactive_terminal("the real-arm bridge")
    Bridge(a).run()


if __name__ == "__main__":
    main()
