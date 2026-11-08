#!/usr/bin/env python3
"""Headless Table 10 real-arm service: real_arm_bridge.Bridge without a terminal.

Started and supervised by DreamSim/table10_agent.py (no separate app to launch), with the
maker-arm-sdk venv python (local gs_usb backend):

  <maker-arm-sdk>/.venv/bin/python Tools/real_arm_service.py --left SERIAL --right SERIAL \
      --state-dir <project>/Saved/RealArmLink

The operator asked for drive to be switched from the web page or VR instead of the bridge
terminal, so this replaces the terminal's m/d keys with a heartbeat file written by the agent:

  control.json  {"unix": t, "drive": bool, "drive_seq": n, "release_seq": n, "source": "web"}

Safety, on top of everything real_arm_bridge.Bridge already does (engage only when Unreal
matches within 5 deg, speed cap, jump rejection, stale-Unreal hold, verified return to the
engage pose before torque off, SDK limit clipping and fault latching):
- Starts in mirror (torque off). Drive engages only on a NEW drive request (drive_seq) with a
  fresh heartbeat; a refused engage is reported and not retried until the next request.
- Dead-man: in drive, a control heartbeat older than DEADMAN_S, drive:false, or Unreal state
  missing for UE_LOST_S returns the arms to their engage pose and releases torque.
- If that return cannot be verified the SDK keeps holding (as in the terminal bridge); a new
  release_seq (web "Torque off" after supporting the arm) releases.
- SIGTERM/SIGINT release the same way before exiting.
Joints 1-6 have no motor watchdog: keep the hardware E-stop within reach whenever drive is on.
"""
import argparse
import io
import json
import signal
import sys
import threading
import time
from pathlib import Path

import real_arm_bridge as rb

DEADMAN_S = 2.0
UE_LOST_S = 5.0


class LastLine(io.TextIOBase):
    """stdout tee: keeps the bridge's last printed line for the status file."""
    def __init__(self, stream):
        self.stream, self.last, self.buffer = stream, '', ''

    def write(self, text):
        self.stream.write(text)
        self.stream.flush()
        self.buffer += text
        while '\n' in self.buffer:
            line, self.buffer = self.buffer.split('\n', 1)
            if line.strip() and not line.startswith('['):  # periodic status lines start with "[mode]"
                self.last = line.strip()
        return len(text)

    def flush(self):
        self.stream.flush()


class Service:
    def __init__(self, args):
        self.bridge = rb.Bridge(args)
        self.dir = Path(args.state_dir)
        self.control_path = self.dir / 'control.json'
        self.status_path = self.dir / 'service.json'
        self.control = {}
        self.release_seq = None
        self.drive_seq = None
        self.ue_seen = time.monotonic()
        self.stopping = False
        self.out = sys.stdout if isinstance(sys.stdout, LastLine) else None

    def read_control(self):
        while not self.stopping:
            try:
                self.control = json.loads(self.control_path.read_text())
            except (FileNotFoundError, ValueError, OSError):
                pass
            seq = self.control.get('release_seq')
            if self.release_seq is None:
                self.release_seq = seq
            elif seq != self.release_seq:
                self.release_seq = seq
                self.bridge.lines.put('release')  # unblocks a held (unverified) return
            time.sleep(.1)

    def write_status(self, error=None):
        last = self.out.last if self.out else ''
        rb.write(self.status_path, {'unix': time.time(), 'running': not self.stopping and error is None,
                                    'mode': self.bridge.mode, 'message': error or last,
                                    'sides': [s.name for s in self.bridge.sides]})

    def run(self):
        for side in self.bridge.sides:
            side.arm.connect()
            print(f'[{side.name}] connected on adapter {side.serial} (headless service, mirror)')
        threading.Thread(target=self.read_control, daemon=True).start()
        last_status = 0.
        try:
            while True:
                started = time.monotonic()
                c = self.control
                fresh = time.time() - float(c.get('unix', 0)) <= DEADMAN_S
                if self.bridge.read_ue() is not None:
                    self.ue_seen = started
                if self.bridge.mode == 'mirror':
                    if c.get('drive') and fresh and c.get('drive_seq') != self.drive_seq:
                        self.drive_seq = c.get('drive_seq')
                        print(f"drive requested from {c.get('source', 'web')}")
                        self.bridge.engage()
                    self.bridge.mirror_step()
                    period = 1.0 / rb.MIRROR_HZ
                else:
                    reason = ('control heartbeat lost' if not fresh else 'drive switched off' if not c.get('drive')
                              else 'Unreal state lost' if started - self.ue_seen > UE_LOST_S else None)
                    if reason:
                        print(f'{reason}: returning to the engage pose and releasing torque')
                        self.bridge.release()
                        print('MIRROR (torque off)')
                        period = 0.
                    else:
                        self.bridge.drive_step()
                        period = 1.0 / rb.DRIVE_HZ
                self.bridge.status()
                if started - last_status > .25:
                    last_status = started
                    self.write_status()
                time.sleep(max(0.0, period - (time.monotonic() - started)))
        finally:
            self.stopping = True
            self.bridge.release()
            for side in self.bridge.sides:
                side.arm.disconnect()
            print('service exited; torque off')
            self.write_status('stopped')


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

    def interrupt(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupt)
    sys.stdout = LastLine(sys.stdout)
    service = Service(args)
    try:
        service.run()
    except KeyboardInterrupt:
        pass
    except Exception as error:
        service.write_status('error: %s' % error)
        raise


if __name__ == '__main__':
    main()
