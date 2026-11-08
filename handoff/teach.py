"""Record a pose (or the can's jaw contact angle) from the live arm into poses.json.

Drive the arm to the pose first with pickup_client.py (joints / tip / nudge),
with the station operator on the e-stop. This reads status only; it never moves.

  python3 -m handoff.teach offer                 # current joints become 'offer', taught
  python3 -m handoff.teach at_mark --speed 8
  python3 -m handoff.teach --contact             # jaws are closed on the can: set the band
  python3 -m handoff.teach --show

Commit poses.json after teaching so the team runs the same poses.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .arm_link import SocketArm
from .config import DEFAULT_POSES, load

BAND_DEG = 8.0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("name", nargs="?")
    p.add_argument("--poses", type=Path, default=DEFAULT_POSES)
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--speed", type=float)
    p.add_argument("--note", default="")
    p.add_argument("--contact", action="store_true", help="record the jaw angle closed on the can")
    p.add_argument("--show", action="store_true")
    a = p.parse_args(argv)
    raw = json.loads(a.poses.read_text())
    if a.show:
        for name, pose in raw["poses"].items():
            print("%-11s %-5s %s" % (name, "ok" if pose.get("taught") else "TEACH", pose["q"]))
        g = raw.get("gripper", {})
        print("contact band %s to %s, %s" % (g.get("contact_min_deg"), g.get("contact_max_deg"),
                                             "measured" if g.get("contact_calibrated") else "NOT MEASURED"))
        return 0
    if not a.name and not a.contact:
        p.error("name a pose, or pass --contact")
    status = SocketArm(a.port).status()
    q = status["q_deg"]
    stamp = datetime.now().astimezone().isoformat(timespec="minutes")
    if a.contact:
        g = raw.setdefault("gripper", {})
        angle = round(q[6], 2)
        g["contact_min_deg"] = round(angle - BAND_DEG, 2)
        g["contact_max_deg"] = round(min(angle + BAND_DEG, g.get("empty_deg", -8.0) - 1.0), 2)
        g["contact_calibrated"] = True
        g["contact_note"] = "measured %.2f deg on the demo can, %s" % (angle, stamp)
        print("contact band %.2f to %.2f deg" % (g["contact_min_deg"], g["contact_max_deg"]))
    else:
        old = raw["poses"].get(a.name, {})
        raw["poses"][a.name] = {
            "q": [round(x, 2) for x in q[:6]],
            "speed": a.speed or old.get("speed", 10),
            "taught": True,
            "note": (a.note or "taught at the station") + ", " + stamp,
        }
        print("%s = %s" % (a.name, raw["poses"][a.name]["q"]))
    a.poses.write_text(json.dumps(raw, indent=2) + "\n")
    load(a.poses)  # still valid
    return 0


if __name__ == "__main__":
    sys.exit(main())
