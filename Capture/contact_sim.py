# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["mujoco==4.0.0", "numpy>=1.26,<3", "scipy>=1.11,<2"]
# ///
"""Run the external Maker Arm contact simulation from registered human pose frames."""

import argparse
import copy
import json
import math
import socket
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import deque
from pathlib import Path

import mujoco
import numpy as np

URDF = (
    Path(__file__).resolve().parents[1]
    / "Unreal/TheHumanTouch/SourceAssets/MakerArm/robot.urdf"
)
SUBJECTS = ("Human_Quest_A", "Human_Cam_B", "Human_Quest_C")
SIDES = ("left", "right")
FRESH_NS = 120_000_000
SIM_HZ = 60
SUBSTEPS = 4


def vector_string(v):
    return " ".join(str(float(x)) for x in v)


def compile_station(layout):
    """Keep fixed bodies (including grasp_center) and the upstream gripper equality."""
    xml = ET.parse(URDF).getroot()
    options = ET.SubElement(xml, "mujoco")
    ET.SubElement(options, "compiler", fusestatic="false")
    assets = {f.name: f.read_bytes() for f in (URDF.parent / "meshes").glob("*.stl")}
    single = mujoco.MjModel.from_xml_string(
        ET.tostring(xml, encoding="unicode"), assets
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = str(Path(tmp) / "canonical.xml")
        mujoco.mj_saveLastXML(path, single)
        scene = ET.parse(path).getroot()
    world = scene.find("worldbody")
    base = world.find("body")
    world.remove(base)
    equalities = scene.find("equality")
    mimic = equalities.find("joint")
    equalities.remove(mimic)
    ET.SubElement(
        scene,
        "option",
        timestep=str(1 / (SIM_HZ * SUBSTEPS)),
        integrator="implicitfast",
    )
    for side in SIDES:
        arm = copy.deepcopy(base)
        p, q = layout["bases"][side]
        if (
            len(p) != 3
            or len(q) != 4
            or not np.isfinite([*p, *q]).all()
            or abs(np.linalg.norm(q) - 1) > 1e-3
        ):
            raise ValueError(f"Invalid base transform for {side}")
        # Source layout is the actual UE base actor transform. Convert to RH Z-up
        # before attaching the original CAD bodies. No additional mounting rotation.
        arm.set("pos", vector_string([p[0] / 100, -p[1] / 100, p[2] / 100]))
        arm.set("quat", vector_string([q[3], -q[0], q[1], -q[2]]))
        for element in arm.iter():
            if "name" in element.attrib:
                element.set("name", side + "_" + element.get("name"))
            if element.tag == "geom":
                element.set("contype", "1")
                element.set(
                    "conaffinity", "6"
                )  # human and token; upstream self-collision unvalidated
        world.append(arm)
        constraint = copy.deepcopy(mimic)
        for key in ("joint1", "joint2"):
            constraint.set(key, side + "_" + constraint.get(key))
        equalities.append(constraint)
    height = float(layout["table_height_cm"]) / 100
    ET.SubElement(
        world,
        "geom",
        name="table",
        type="plane",
        size="2 2 .02",
        pos=vector_string([0, 0, height]),
        contype="8",
        conaffinity="4",
    )
    token = ET.SubElement(
        world, "body", name="token", pos=vector_string([0.46, 0, height + 0.023])
    )
    ET.SubElement(token, "freejoint", name="token_free")
    ET.SubElement(
        token,
        "geom",
        type="box",
        size=".022 .022 .022",
        mass=".04",
        contype="4",
        conaffinity="11",
    )
    for who in SUBJECTS:
        for side in SIDES:
            for i in range(5):
                name = f"{who}/{side}/{i}"
                body = ET.SubElement(
                    world, "body", name=name, mocap="true", pos="0 0 -10"
                )
                ET.SubElement(
                    body,
                    "geom",
                    type="sphere",
                    size=".045" if i == 4 else ".038",
                    contype="2",
                    conaffinity="5",
                )
    return mujoco.MjModel.from_xml_string(
        ET.tostring(scene, encoding="unicode"), assets
    )


def xr_to_sim(v):
    return np.array([-v[2], -v[0], v[1]])


def ue_pos(v):
    return [round(100 * v[0], 3), round(-100 * v[1], 3), round(100 * v[2], 3)]


class Samples:
    def __init__(self, space):
        self.space = space
        self.frames = {who: deque(maxlen=48) for who in SUBJECTS}

    def receive(self, frame, now):
        who = frame["subject"]
        if (
            who not in self.frames
            or frame["space"] != self.space
            or frame["version"] != 1
        ):
            return False
        if (
            not isinstance(frame.get("session"), str)
            or not frame["session"]
            or not str(frame.get("seq", "")).isdecimal()
        ):
            return False
        stamp = int(frame["station_ns"])
        if not -50_000_000 <= now - stamp <= FRESH_NS:
            return False
        queue = self.frames[who]
        if queue and stamp <= int(queue[-1]["station_ns"]):
            return False
        bones = frame["bones"]
        if not isinstance(bones, list) or not 1 <= len(bones) <= 100:
            return False
        for i, row in enumerate(bones):
            if (
                len(row) != 10
                or not isinstance(row[0], str)
                or type(row[1]) is not int
                or (row[1] != -1 if i == 0 else not 0 <= row[1] < i)
                or not all(
                    isinstance(x, (int, float)) and math.isfinite(x) for x in row[2:]
                )
                or not 0 <= row[9] <= 1
                or max(map(abs, row[2:5])) > 100
            ):
                return False
        if queue and queue[-1]["session"] != frame["session"]:
            queue.clear()
        queue.append(frame)
        return True

    def at(self, target):
        out = {}
        for who, queue in self.frames.items():
            for frame in reversed(queue):
                age = target - int(frame["station_ns"])
                if age < 0:
                    continue
                if age <= FRESH_NS:
                    out[who] = frame
                break
        return out


class Station:
    def __init__(self, layout):
        self.model = compile_station(layout)
        self.data = mujoco.MjData(self.model)
        self.tick = 0
        self.controls = []
        self.links = {}
        self.proxies = {}
        self.last_positions = {}
        self.armed = set()
        for side in SIDES:
            for i in range(2, 8):
                self.controls.append(
                    (self.model.joint(f"{side}_link_{i:03}_joint"), 0.0, 4.0, 0.35)
                )
            for jaw in SIDES:
                joint = self.model.joint(f"{side}_gripper_{jaw}_joint")
                self.controls.append((joint, 0.02, 80.0, 1.0))
                self.data.qpos[joint.qposadr[0]] = 0.02
            for link in (
                "base_link",
                *(f"link_{i:03}" for i in range(2, 8)),
                "gripper_left",
                "gripper_right",
                "grasp_center",
            ):
                name = f"{side}_{link}"
                self.links[name] = self.model.body(name).id
        for who in SUBJECTS:
            for side in SIDES:
                self.proxies[(who, side)] = [
                    self.model.body(f"{who}/{side}/{i}").mocapid[0] for i in range(5)
                ]
        mujoco.mj_forward(self.model, self.data)

    def step(self, selected, target_ns):
        m, d = self.model, self.data
        self.tick += 1
        applied = {}
        for who in SUBJECTS:
            frame = selected.get(who)
            points = (
                {r[0]: xr_to_sim(r[2:5]) for r in frame["bones"] if r[9] >= 0.5}
                if frame
                else {}
            )
            applied[who] = []
            for side in SIDES:
                ids = self.proxies[who, side]
                elbow = points.get(
                    f"{side}-arm-lower" if who != "Human_Cam_B" else f"{side}_forearm"
                )
                hand = points.get(
                    f"{side}-hand-wrist" if who != "Human_Cam_B" else f"{side}_hand"
                )
                key = (who, side)
                old = self.last_positions.get(key)
                if (
                    hand is None
                    or elbow is None
                    or not 0.06 <= np.linalg.norm(hand - elbow) <= 0.55
                ):
                    d.mocap_pos[ids] = [0, 0, -10]
                    self.last_positions.pop(key, None)
                    self.armed.discard(key)
                    continue
                sample_id = (frame["session"], frame["seq"])
                if old is None or sample_id != old[0]:
                    if (
                        old is None
                        or sample_id[0] != old[0][0]
                        or max(
                            np.linalg.norm(hand - old[1]),
                            np.linalg.norm(elbow - old[2]),
                        )
                        > 0.20
                    ):
                        self.armed.discard(key)
                    else:
                        self.armed.add(key)
                    self.last_positions[key] = (sample_id, hand.copy(), elbow.copy())
                if key not in self.armed:
                    d.mocap_pos[ids] = [0, 0, -10]
                    continue
                d.mocap_pos[ids] = np.linspace(elbow, hand, len(ids))
                applied[who].append(side)
        contacts = {}
        for _ in range(SUBSTEPS):
            # Provisional compliant position hold for a simulation demo. Only
            # generalized forces inside MuJoCo are affected.
            for joint, rest, kp, kd in self.controls:
                j = joint.dofadr[0]
                d.qfrc_applied[j] = (
                    d.qfrc_bias[j]
                    + kp * (rest - d.qpos[joint.qposadr[0]])
                    - kd * d.qvel[j]
                )
            mujoco.mj_step(m, d)
            for contact in d.contact:
                b1, b2 = (m.body(m.geom_bodyid[g]).name for g in contact.geom)
                if "/" not in b1 and "/" not in b2:
                    continue
                proxy, other = (b1, b2) if "/" in b1 else (b2, b1)
                who, side, _ = proxy.split("/")
                contacts[who, side, other] = [who, side, other, ue_pos(contact.pos)]
        mujoco.mj_forward(m, d)

        def pose(body):
            w, x, y, z = d.xquat[body]
            return [
                *ue_pos(d.xpos[body]),
                round(-x, 6),
                round(y, 6),
                round(-z, 6),
                round(w, 6),
            ]

        inputs = {
            who: {k: frame[k] for k in ("seq", "session", "station_ns", "space")}
            for who, frame in selected.items()
        }
        arms = {name: pose(body) for name, body in self.links.items()}
        segments = [
            [
                who,
                side,
                ue_pos(d.mocap_pos[self.proxies[who, side][0]]),
                ue_pos(d.mocap_pos[self.proxies[who, side][-1]]),
            ]
            for who, sides in applied.items()
            for side in sides
        ]
        return {
            "version": 1,
            "tick": self.tick,
            "target_ns": str(target_ns),
            "inputs": inputs,
            "arms": arms,
            "colliders": segments,
            "contacts": list(contacts.values()),
            "token": pose(m.body("token").id),
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, required=True)
    parser.add_argument("--delay-ms", type=int, default=45)
    parser.add_argument("--input-port", type=int, default=17861)
    parser.add_argument("--output-port", type=int, default=17863)
    parser.add_argument("--ticks", type=int, default=0, help="0 runs until interrupted")
    args = parser.parse_args()
    layout = json.loads(args.layout.read_text())
    if (
        not 0 <= args.delay_ms <= 100
        or not layout["space"]
        or layout["space"] in ("hip_relative", "quest_local_floor")
    ):
        parser.error("Use a registered table space and a delay of 0–100 ms")
    station = Station(layout)
    samples = Samples(layout["space"])
    with (
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp,
        socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as tx,
    ):
        udp.bind(("127.0.0.1", args.input_port))
        udp.setblocking(False)
        tx.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65535)
        deadline = time.monotonic()
        while not args.ticks or station.tick < args.ticks:
            time.sleep(max(0, deadline - time.monotonic()))
            now = time.monotonic_ns()
            for _ in range(192):
                try:
                    packet, _ = udp.recvfrom(65535)
                except BlockingIOError:
                    break
                try:
                    samples.receive(json.loads(packet), now)
                except (TypeError, ValueError, KeyError, IndexError, OverflowError):
                    continue
            target = now - args.delay_ms * 1_000_000
            state = station.step(samples.at(target), target)
            tx.sendto(
                json.dumps(state, separators=(",", ":"), allow_nan=False).encode(),
                ("127.0.0.1", args.output_port),
            )
            deadline = max(deadline + 1 / SIM_HZ, time.monotonic())


if __name__ == "__main__":
    main()
