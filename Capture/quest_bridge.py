# /// script
# requires-python = ">=3.11,<3.13"
# dependencies = ["aiohttp>=3.12,<4", "numpy>=1.26,<3", "scipy>=1.11,<2"]
# ///
"""Serve the Quest XR capture page over USB adb reverse and relay synchronized poses locally."""

import argparse
import asyncio
import json
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


def encode_frame(msg, session, offset_ns, rtt_ns, anchor):
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
        "subject": "Human_Quest_A",
        "session": session,
        "seq": str(seq),
        "space": space,
        "station_ns": str(station_ns),
        "source_ms": sample_ms,
        "timing": "xr_predicted_display",
        "clock_rtt_ns": rtt_ns,
        "bones": bones,
    }
    return station_ns, json.dumps(data, separators=(",", ":"), allow_nan=False).encode()


def create_app(observer_port, anchor, *, livelink_port=17860):
    app = web.Application()
    active = asyncio.Lock()

    async def home(_):
        return web.FileResponse(Path(__file__).with_name("quest.html"))

    async def receive(request):
        # The station only serves localhost (USB reverse). Reject cross-site browser connections.
        if request.headers.get("Origin") != f"{request.scheme}://{request.host}":
            raise web.HTTPForbidden()
        if active.locked():
            raise web.HTTPConflict(text="A Quest performer is already connected")
        async with active:
            ws = web.WebSocketResponse(max_msg_size=60000, heartbeat=10)
            await ws.prepare(request)
            sent = {}
            samples = deque(maxlen=12)
            session = uuid.uuid4().hex
            last_ns = last_seq = -1

            async def ping():
                while True:
                    now = time.monotonic_ns()
                    sent.clear()
                    sent[str(now)] = now
                    await ws.send_json({"ping": str(now)})
                    await asyncio.sleep(0.4 if len(samples) < 4 else 2.0)

            task = asyncio.create_task(ping())
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
                    async for packet in ws:
                        if packet.type != web.WSMsgType.TEXT:
                            break
                        msg = json.loads(packet.data)
                        if "pong" in msg:
                            t1 = sent.pop(msg["pong"], None)
                            t2 = time.monotonic_ns()
                            client_ns = int(float(msg["client_ms"]) * 1e6)
                            if t1 and t2 - t1 < 100_000_000:
                                samples.append((t2 - t1, (t1 + t2) // 2 - client_ns))
                        elif samples and msg["seq"] > last_seq:
                            rtt, offset = min(samples)
                            stamp, encoded = encode_frame(
                                msg, session, offset, rtt, anchor
                            )
                            if stamp > last_ns:
                                for port in (livelink_port, observer_port):
                                    udp.sendto(encoded, ("127.0.0.1", port))
                                last_ns, last_seq = stamp, msg["seq"]
            except (ValueError, KeyError, TypeError, OverflowError, IndexError) as exc:
                await ws.send_json({"error": str(exc)})
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError, ConnectionError):
                    await task
                await ws.close()
            return ws

    app.add_routes([web.get("/", home), web.get("/pose", receive)])
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=17862)
    parser.add_argument("--observer-port", type=int, default=17861)
    parser.add_argument(
        "--calibration",
        type=Path,
        help="JSON with space and measured table_from_local_floor 4x4",
    )
    args = parser.parse_args()
    if args.observer_port == 17860 or not all(
        0 < p < 65536 for p in (args.port, args.observer_port)
    ):
        parser.error("Invalid port")
    anchor = None
    if args.calibration:
        data = json.loads(args.calibration.read_text())
        t = np.array(data["table_from_local_floor"], dtype=float)
        if (
            not data["space"]
            or t.shape != (4, 4)
            or not np.isfinite(t).all()
            or not np.allclose(t[3], [0, 0, 0, 1])
            or not np.allclose(t[:3, :3].T @ t[:3, :3], np.eye(3), atol=1e-4)
            or abs(np.linalg.det(t[:3, :3]) - 1) > 1e-4
        ):
            parser.error("Invalid table registration")
        anchor = data["space"], Rotation.from_matrix(t[:3, :3]), t[:3, 3]
    web.run_app(
        create_app(args.observer_port, anchor), host="127.0.0.1", port=args.port
    )
