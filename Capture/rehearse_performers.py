"""Send a deterministic camera/headset rehearsal to the local Hub; no device is opened."""

import argparse
import json
import math
import socket
import time
import uuid

SUBJECTS = ("Human_Quest_A", "Human_Cam_B", "Human_Quest_C")
POSES = ("neutral", "reach", "curl", "squat", "step", "neutral")
IDENTITY = (0.0, 0.0, 0.0, 1.0)


def skeleton(subject, pose):
    """Positions are absolute XR metres. Keep both ankles planted during squat and step."""
    quest = subject != "Human_Cam_B"
    lowered = 0.16 if pose == "squat" else 0.0
    travel = 0.10 if pose == "step" else 0.0
    rows = []

    def joint(name, parent, xyz, orientation=IDENTITY):
        rows.append([name, parent, *xyz, *orientation, 1.0])
        return len(rows) - 1

    hip = joint("hips" if quest else "pelvis", -1, (travel, 1.01 - lowered, 0.0))
    spine = hip
    if quest:
        for i, part in enumerate(("spine-lower", "spine-middle", "spine-upper"), 1):
            spine = joint(part, spine, (travel, 1.01 + i * 0.10 - lowered, 0.0))
    chest = joint("chest", spine, (travel, 1.44 - lowered, 0.0))
    neck = joint("neck", chest, (travel, 1.55 - lowered, 0.0)) if quest else chest
    angle = math.radians(40 if pose in ("reach", "curl") else 0)
    joint(
        "head",
        neck,
        (travel, 1.70 - lowered, 0.0),
        (0.0, math.sin(angle / 2), 0.0, math.cos(angle / 2)),
    )
    for side, sign in (("left", -1), ("right", 1)):
        reaching = side == "left" and pose in ("reach", "curl")
        parent = (
            joint(
                side + "-shoulder", chest, (travel + sign * 0.08, 1.44 - lowered, 0.0)
            )
            if quest
            else chest
        )
        upper = joint(
            side + ("-arm-upper" if quest else "_upper_arm"),
            parent,
            (travel + sign * 0.19, 1.44 - lowered, 0.0),
        )
        elbow = joint(
            side + ("-arm-lower" if quest else "_forearm"),
            upper,
            (
                travel + sign * (0.43 if reaching else 0.30),
                (1.46 if reaching else 1.18) - lowered,
                -0.035,
            ),
        )
        wrist = (
            travel + sign * (0.65 if reaching else 0.36),
            (1.49 if reaching else 0.96) - lowered,
            -0.08,
        )
        if quest:
            elbow = joint(side + "-hand-wrist-twist", elbow, wrist)
        hand = joint(side + ("-hand-wrist" if quest else "_hand"), elbow, wrist)
        if quest:
            joint(side + "-hand-palm", hand, (wrist[0], wrist[1] - 0.03, wrist[2]))
        for i, finger in enumerate(("thumb", "index", "middle", "ring", "little")):
            if not quest and finger not in ("thumb", "index", "little"):
                continue
            parts = (
                [
                    "metacarpal",
                    "phalanx-proximal",
                    "phalanx-intermediate",
                    "phalanx-distal",
                    "tip",
                ]
                if quest
                else ["tip"]
            )
            if finger == "thumb" and quest:
                parts.remove("phalanx-intermediate")
            parent = hand
            for j, part in enumerate(parts, 1):
                curled = (
                    pose == "curl" and side == "left" and finger == "index" and j > 2
                )
                length = j * 0.019 if quest else 0.095
                along = 0.04 if curled else length
                point = (
                    wrist[0] + (sign * along if reaching else sign * 0.01 * i),
                    wrist[1] + (0.0 if reaching else -along),
                    wrist[2] + (i - 2) * 0.018 - (length - 0.04 if curled else 0.0),
                )
                name = (
                    f"{side}-hand-{finger}-{part}"
                    if quest
                    else f"{side}_{'pinky' if finger == 'little' else finger}"
                )
                parent = joint(name, parent, point)
        if not quest:
            thigh = joint(
                side + "_thigh", hip, (travel + sign * 0.10, 1.01 - lowered, 0.0)
            )
            shin = joint(
                side + "_shin",
                thigh,
                (sign * 0.11, 0.53 - lowered * 0.4, -0.23 if lowered else -0.025),
            )
            ankle = joint(side + "_foot", shin, (sign * 0.11, 0.075, 0.0))
            joint(side + "_toe", ankle, (sign * 0.11, 0.04, -0.15))
    return rows


def publish(sock, subject, pose, session, seq, port=17860):
    frame = {
        "version": 1,
        "subject": subject,
        "session": session,
        "seq": str(seq),
        "station_ns": str(time.monotonic_ns()),
        "space": "hip_relative" if subject == "Human_Cam_B" else "quest_local_floor",
        "timing": "synthetic_rehearsal",
        "bones": skeleton(subject, pose),
    }
    sock.sendto(json.dumps(frame, separators=(",", ":")).encode(), ("127.0.0.1", port))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--subject", choices=SUBJECTS, help="Defaults to all three Hub subjects"
    )
    parser.add_argument("--seconds-per-pose", type=float, default=1.0)
    args = parser.parse_args()
    if (
        not math.isfinite(args.seconds_per_pose)
        or not 0.1 <= args.seconds_per_pose <= 60
    ):
        parser.error("--seconds-per-pose must be between 0.1 and 60")
    subjects = [args.subject] if args.subject else SUBJECTS
    session = "rehearsal-" + uuid.uuid4().hex
    seq = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65535)
        for pose in POSES:
            print(pose, flush=True)
            until = time.monotonic() + args.seconds_per_pose
            while time.monotonic() < until:
                for subject in subjects:
                    publish(sock, subject, pose, session, seq)
                seq += 1
                time.sleep(1 / 30)
