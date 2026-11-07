# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["aiohttp>=3.12,<4", "numpy>=1.26,<3", "scipy>=1.11,<2"]
# ///
"""Serve the Quest XR capture page over USB adb reverse and relay synchronized poses locally."""

import argparse
import asyncio
import json
import ipaddress
import logging
import math
import socket
import time
import uuid
from collections import deque
from contextlib import suppress
from pathlib import Path

import numpy as np
from aiohttp import web
from scipy.spatial.transform import Rotation


def encode_frame(msg, session, offset_ns, rtt_ns, anchor, subject="Human_Quest_A"):
    sample_ms = msg["client_ms"]
    if not isinstance(sample_ms, (int, float)) or not math.isfinite(sample_ms):
        raise ValueError("Invalid headset time")
    station_ns = int(sample_ms * 1e6 + offset_ns)
    if abs(time.monotonic_ns() - station_ns) > 500_000_000:
        raise ValueError("Headset timestamp outside the synchronization window")
    seq, bones = msg["seq"], msg["bones"]
    if type(seq) is not int or seq < 0 or not 2 <= len(bones) <= 100:
        raise ValueError("Invalid frame")
    names = set()
    for i, row in enumerate(bones):
        if len(row) != 10:
            raise ValueError("Invalid bone")
        name, parent, *numbers = row
        if (
            not isinstance(name, str)
            or len(name) > 64
            or not name
            or name in names
            or type(parent) is not int
            or (parent != -1 if i == 0 else not 0 <= parent < i)
        ):
            raise ValueError("Invalid skeleton hierarchy")
        names.add(name)
        if not all(type(x) in (int, float) and math.isfinite(x) for x in numbers):
            raise ValueError("Non-finite joint")
        if abs(sum(x * x for x in numbers[3:7]) - 1) > 0.02 or not 0 <= numbers[7] <= 1:
            raise ValueError("Invalid orientation or validity")
    if anchor:
        space, basis, shift = anchor
        positions = basis.apply([row[2:5] for row in bones]) + shift
        rotations = (basis * Rotation.from_quat([row[5:9] for row in bones])).as_quat()
        bones = [
            [*row[:2], *p, *q, row[9]]
            for row, p, q in zip(bones, positions.tolist(), rotations.tolist())
        ]
    else:
        space = "quest_local_floor"
    data = {
        "version": 1,
        "subject": subject,
        "session": session,
        "seq": str(seq),
        "space": space,
        "station_ns": str(station_ns),
        "source_ms": sample_ms,
        "timing": "editor_xr_sample"
        if subject == "Human_Quest_C"
        else "xr_predicted_display",
        "clock_rtt_ns": rtt_ns,
        "bones": bones,
    }
    if "optical_joints" in msg:
        counts = msg["optical_joints"]
        if not isinstance(counts, dict) or any(
            type(counts.get(s)) is not int or not 0 <= counts[s] <= 25
            for s in ("left", "right")
        ):
            raise ValueError("Invalid optical hand joint counts")
        data["optical_joints"] = {s: counts[s] for s in ("left", "right")}
    tip_field = "xr_hand_tips" if subject == "Human_Quest_C" else "optical_index_tips"
    if tip_field in msg:
        tips = msg[tip_field]
        if not isinstance(tips, dict) or any(
            type(tips.get(s)) is not bool for s in ("left", "right")
        ):
            raise ValueError("Invalid optical fingertip flags")
        data[tip_field] = {s: tips[s] for s in ("left", "right")}
    encoded = json.dumps(data, separators=(",", ":"), allow_nan=False).encode()
    if len(encoded) > 60000:
        raise ValueError("Pose exceeds the Live Link receiver's packet limit")
    return station_ns, encoded


DEFAULT_SESSION_REGISTRATION = (
    Path(__file__).resolve().parents[1]
    / "Unreal/TheHumanTouch/Saved/Capture/quest-session-registration.json"
)


def read_anchor(data):
    t = np.asarray(data["table_from_local_floor"], dtype=float)
    if (
        not isinstance(data["space"], str)
        or data["space"] in ("", "hip_relative", "quest_local_floor")
        or t.shape != (4, 4)
        or not np.isfinite(t).all()
        or not np.allclose(t[3], [0, 0, 0, 1])
        or not np.allclose(t[:3, :3].T @ t[:3, :3], np.eye(3), atol=1e-4)
        or abs(np.linalg.det(t[:3, :3]) - 1) > 1e-4
    ):
        raise ValueError("Invalid table registration")
    return data["space"], Rotation.from_matrix(t[:3, :3]), t[:3, 3]


class SessionRegistration:
    """Never apply a local-floor fit to a later immersive session."""

    def __init__(self, path, session, fallback):
        self.path = path
        self.session = session
        self.fallback = fallback
        self.anchor = fallback
        self.next_read = 0.0
        self.signature = None

    def current(self):
        now = time.monotonic()
        if not self.path or now < self.next_read:
            return self.anchor
        self.next_read = now + 0.25
        try:
            stat = self.path.stat()
            signature = stat.st_mtime_ns, stat.st_ino
            if signature != self.signature:
                self.signature = signature
                data = json.loads(self.path.read_text())
                self.anchor = (
                    read_anchor(data)
                    if data.get("source_session") == self.session
                    else self.fallback
                )
        except FileNotFoundError:
            self.signature = None
            self.anchor = self.fallback
        except (OSError, TypeError, KeyError, ValueError) as error:
            self.anchor = self.fallback
            logging.getLogger(__name__).warning(
                "Could not use Quest session registration: %s", error
            )
        return self.anchor


async def relay_session(messages, send, subject, ports, anchor, registration_file):
    session = uuid.uuid4().hex
    registration = SessionRegistration(registration_file, session, anchor)
    samples = deque(maxlen=12)
    pending = {}
    last_ns = last_seq = -1

    async def ping():
        while True:
            now = time.monotonic_ns()
            pending.clear()
            pending[str(now)] = now
            await send({"ping": str(now)})
            await asyncio.sleep(0.4 if len(samples) < 4 else 2.0)

    task = asyncio.create_task(ping())
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
            udp.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65535)
            async for msg in messages:
                if "pong" in msg:
                    t1 = pending.pop(msg["pong"], None)
                    t2 = time.monotonic_ns()
                    client_ns = int(float(msg["client_ms"]) * 1e6)
                    if t1 and t2 - t1 < 100_000_000:
                        samples.append((t2 - t1, (t1 + t2) // 2 - client_ns))
                elif samples and msg["seq"] > last_seq:
                    rtt, offset = min(samples)
                    stamp, encoded = encode_frame(
                        msg, session, offset, rtt, registration.current(), subject
                    )
                    if stamp > last_ns:
                        for port in ports:
                            udp.sendto(encoded, ("127.0.0.1", port))
                        last_ns, last_seq = stamp, msg["seq"]
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError, ConnectionError):
            await task


def create_app(
    observer_port, anchor, *, livelink_port=17860, session_registration=None
):
    app = web.Application()
    active = asyncio.Lock()

    async def home(_):
        return web.FileResponse(Path(__file__).with_name("quest.html"))

    async def receive(request):
        if request.headers.get("Origin") != f"{request.scheme}://{request.host}":
            raise web.HTTPForbidden()
        if active.locked():
            raise web.HTTPConflict(text="A Quest performer is already connected")
        async with active:
            ws = web.WebSocketResponse(max_msg_size=60000, heartbeat=10)
            await ws.prepare(request)

            async def messages():
                async for packet in ws:
                    if packet.type != web.WSMsgType.TEXT:
                        return
                    yield json.loads(packet.data)

            try:
                await relay_session(
                    messages(),
                    ws.send_json,
                    "Human_Quest_A",
                    (livelink_port, observer_port),
                    anchor,
                    session_registration,
                )
            except (ValueError, KeyError, TypeError, OverflowError, IndexError) as exc:
                await ws.send_json({"error": str(exc)})
            finally:
                await ws.close()
            return ws

    app.add_routes([web.get("/", home), web.get("/pose", receive)])
    return app


async def editor_listener(address, peer, port, ports, registration):
    active = asyncio.Lock()

    async def accept(reader, writer):
        async def messages():
            while line := await asyncio.wait_for(reader.readline(), 8):
                yield json.loads(line)

        async def send(message):
            writer.write(json.dumps(message).encode() + b"\n")
            await writer.drain()

        try:
            if writer.get_extra_info("peername")[0] != peer or active.locked():
                return
            async with active:
                await relay_session(
                    messages(), send, "Human_Quest_C", ports, None, registration
                )
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            OverflowError,
            IndexError,
        ) as exc:
            logging.info("Quest 2 editor connection ended: %s", exc)
        finally:
            writer.close()
            with suppress(ConnectionError):
                await writer.wait_closed()

    return await asyncio.start_server(accept, address, port, limit=60000)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=17862)
    parser.add_argument("--observer-port", type=int, default=17861)
    parser.add_argument(
        "--calibration",
        type=Path,
        help="JSON with space and measured table_from_local_floor 4x4",
    )
    parser.add_argument(
        "--session-registration",
        type=Path,
        default=DEFAULT_SESSION_REGISTRATION,
        help="Reload this touch registration while its source session is active",
    )
    parser.add_argument(
        "--editor-peer",
        type=ipaddress.IPv4Address,
        help="Allow this XR editor on a dedicated TCP listener for Human_Quest_C",
    )
    parser.add_argument(
        "--listen-address",
        type=ipaddress.IPv4Address,
        help="Station Tailscale IPv4 address for the XR editor listener",
    )
    args = parser.parse_args()
    if bool(args.editor_peer) != bool(args.listen_address):
        parser.error("--editor-peer and --listen-address must be specified together")
    if args.editor_peer:
        tailnet = ipaddress.ip_network("100.64.0.0/10")
        if args.listen_address not in tailnet or args.editor_peer not in tailnet:
            parser.error("Use the two Tailscale IPv4 addresses for the editor listener")
        if args.calibration:
            parser.error("Use a separate session registration for the Quest 2")
    if args.observer_port == 17860 or not all(
        0 < p < 65536 for p in (args.port, args.observer_port)
    ):
        parser.error("Invalid port")
    anchor = None
    if args.calibration:
        data = json.loads(args.calibration.read_text())
        if "source_session" in data:
            parser.error(
                "Use --session-registration for a session-bound touch registration"
            )
        anchor = read_anchor(data)
    if args.editor_peer:
        path = args.session_registration
        if path == DEFAULT_SESSION_REGISTRATION:
            path = path.with_name("quest2-session-registration.json")

        async def serve():
            server = await editor_listener(
                str(args.listen_address),
                str(args.editor_peer),
                args.port,
                (17860, args.observer_port),
                path,
            )
            async with server:
                await server.serve_forever()

        asyncio.run(serve())
        raise SystemExit
    web.run_app(
        create_app(
            args.observer_port, anchor, session_registration=args.session_registration
        ),
        host="127.0.0.1",
        port=args.port,
    )
