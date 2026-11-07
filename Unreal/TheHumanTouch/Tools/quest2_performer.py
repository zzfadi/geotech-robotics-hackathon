"""Publish the local XR editor's head and hands to the station's Quest 2 listener."""

import asyncio
import json
import math
import threading
import time

import unreal

_capture = None
IDENTITY = (0, 0, 0, 1)


def _position(vector, scale):
    return (vector.y / scale, vector.z / scale, -vector.x / scale)


def _rotation(quat):
    return (-quat.y, -quat.z, quat.x, quat.w)


def _elbow(shoulder, wrist, outward):
    # Two equal-length upper/lower arm segments, with an outward/downward bend pole.
    delta = tuple(b - a for a, b in zip(shoulder, wrist))
    distance = math.hypot(*delta)
    if distance < 0.001:
        return shoulder
    direction = tuple(d / distance for d in delta)
    pole = (outward[0], -0.8, outward[2])
    projection = sum(a * b for a, b in zip(direction, pole))
    bend = tuple(a - projection * b for a, b in zip(pole, direction))
    height = math.sqrt(max(0, 0.31**2 - (distance / 2) ** 2))
    amount = height / max(0.001, math.hypot(*bend))
    return tuple(a + d / 2 + b * amount for a, d, b in zip(shoulder, delta, bend))


def sample(world):
    xr = unreal.HeadMountedDisplayFunctionLibrary
    if not xr.has_valid_tracking_position():
        return None
    scale = xr.get_world_to_meters_scale(world)
    if scale <= 0:
        return None
    stamp = time.monotonic_ns() / 1e6
    rotation, vector = xr.get_orientation_and_position()
    head = _position(vector, scale)
    heading = math.radians(rotation.yaw)
    right = (math.cos(heading), 0, math.sin(heading))
    bones = []
    tracked_tips = {}

    def add(name, parent, xyz, orientation=IDENTITY, confidence=0.45):
        bones.append([name, parent, *xyz, *orientation, confidence])
        return len(bones) - 1

    def below(metres):
        return (head[0], head[1] - metres, head[2])

    spine = add("hips", -1, below(0.72))
    for name, height in zip(
        ("spine-lower", "spine-middle", "spine-upper", "chest"),
        (0.60, 0.47, 0.36, 0.26),
    ):
        spine = add(name, spine, below(height))
    neck = add("neck", spine, below(0.14))
    add("head", neck, head, _rotation(rotation.quaternion()), 1)
    space = unreal.XRSpaceType.XR_TRACKING_SPACE
    for side, hand, sign in (
        ("left", unreal.ControllerHand.LEFT, -1),
        ("right", unreal.ControllerHand.RIGHT, 1),
    ):
        outward = tuple(sign * x for x in right)
        shoulder = tuple(a + 0.19 * b for a, b in zip(below(0.24), outward))
        arm = add(side + "-arm-upper", spine, shoulder)
        tracked = xr.get_hand_tracking_state(world, space, hand)
        hand_joints = (
            tracked.valid
            and tracked.tracking_status == unreal.TrackingStatus.TRACKED
            and len(tracked.hand_key_locations) == len(tracked.hand_key_rotations) == 26
        )
        tracked_tips[side] = bool(hand_joints)
        wrist = shoulder
        orientation = IDENTITY
        confidence = 0
        if hand_joints:
            wrist = _position(tracked.hand_key_locations[1], scale)
            orientation = _rotation(tracked.hand_key_rotations[1])
            confidence = 1
        else:
            grip = xr.get_motion_controller_state(
                world, space, hand, unreal.XRControllerPoseType.GRIP
            )
            if grip.valid and grip.tracking_status == unreal.TrackingStatus.TRACKED:
                wrist = _position(grip.controller_location, scale)
                orientation = _rotation(grip.controller_rotation)
                confidence = 1
        elbow = add(
            side + "-arm-lower",
            arm,
            _elbow(shoulder, wrist, outward),
            confidence=0.45 if confidence else 0,
        )
        root = add(side + "-hand-wrist", elbow, wrist, orientation, confidence)
        index = 2  # EHandKeypoint order starts with palm and wrist.
        for finger in ("thumb", "index", "middle", "ring", "little"):
            parent = root
            parts = ["metacarpal", "phalanx-proximal"]
            if finger != "thumb":
                parts.append("phalanx-intermediate")
            for part in (*parts, "phalanx-distal", "tip"):
                point = (
                    _position(tracked.hand_key_locations[index], scale)
                    if hand_joints
                    else wrist
                )
                quat = (
                    _rotation(tracked.hand_key_rotations[index])
                    if hand_joints
                    else IDENTITY
                )
                parent = add(
                    f"{side}-hand-{finger}-{part}",
                    parent,
                    point,
                    quat,
                    int(bool(hand_joints)),
                )
                index += 1
    return {"client_ms": stamp, "bones": bones, "xr_hand_tips": tracked_tips}


class Capture:
    def __init__(self, address, port):
        self.address = address
        self.port = port
        self.stop = threading.Event()
        self.latest = None
        self.seq = 0
        self.sent = 0
        self.last_tick = 0
        self.status = "Connecting"
        self.thread = threading.Thread(
            target=lambda: asyncio.run(self.run()), daemon=True
        )

    def tick(self, _delta):
        now = time.monotonic()
        if now - self.last_tick < 1 / 30:
            return
        self.last_tick = now
        worlds = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        world = worlds.get_game_world() or worlds.get_editor_world()
        frame = sample(world) if world else None
        if frame:
            self.seq += 1
            self.latest = dict(frame, seq=self.seq)
        else:
            self.latest = None

    async def run(self):
        while not self.stop.is_set():
            writer = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.address, self.port), 3
                )
                self.status = "Waiting for station clock"
                last = -1
                while not self.stop.is_set():
                    try:
                        line = await asyncio.wait_for(reader.readline(), 1 / 60)
                        if not line:
                            self.status = "Station closed connection"
                            break
                        msg = json.loads(line)
                        if "ping" in msg:
                            self.status = "Connected"
                            pong = {
                                "pong": msg["ping"],
                                "client_ms": time.monotonic_ns() / 1e6,
                            }
                            writer.write(json.dumps(pong).encode() + b"\n")
                    except TimeoutError:
                        pass
                    frame = self.latest
                    if (
                        self.status == "Connected"
                        and frame
                        and frame["seq"] != last
                        and abs(time.monotonic_ns() / 1e6 - frame["client_ms"]) < 100
                    ):
                        writer.write(
                            json.dumps(frame, separators=(",", ":")).encode() + b"\n"
                        )
                        last = frame["seq"]
                        self.sent += 1
                    await asyncio.wait_for(writer.drain(), 1)
            except (OSError, ValueError) as error:
                self.status = str(error)
            finally:
                if writer:
                    writer.close()
            await asyncio.sleep(0.5)


def stop():
    global _capture
    if _capture:
        unreal.unregister_slate_post_tick_callback(_capture.handle)
        _capture.stop.set()
        _capture = None


def start(station, port=17865):
    global _capture
    stop()
    _capture = Capture(station, port)
    _capture.handle = unreal.register_slate_post_tick_callback(_capture.tick)
    _capture.thread.start()


def status():
    return (
        {
            "network": _capture.status,
            "headset_frames": _capture.seq,
            "frames_sent": _capture.sent,
        }
        if _capture
        else {"network": "Stopped"}
    )
