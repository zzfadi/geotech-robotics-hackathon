"""Exercise both UDP consumers with a full-precision frame larger than the Mac default."""

import asyncio
import json
import math
import socket
import time
import unittest

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
from quest_bridge import create_app


class QuestTransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_full_rig_reaches_both_consumers_with_identical_time(self):
        loop = asyncio.get_running_loop()
        with (
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as observer,
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as unreal,
        ):
            for udp in (observer, unreal):
                udp.bind(("127.0.0.1", 0))
                udp.setblocking(False)
            app = create_app(
                observer.getsockname()[1], None, livelink_port=unreal.getsockname()[1]
            )
            async with TestServer(app) as server, ClientSession() as client:
                origin = str(server.make_url("/")).rstrip("/")
                async with client.ws_connect(
                    server.make_url("/pose"), origin=origin
                ) as ws:
                    probe = await ws.receive_json()
                    await ws.send_json({"pong": probe["ping"], "client_ms": 1000})
                    bones = []
                    for i in range(67):
                        p = math.sin(i + 0.23)
                        angle = (i + 0.54) / 67
                        q = math.sin(angle) / math.sqrt(3)
                        bones.append(
                            [
                                f"sample-articulated-joint-{i}",
                                i - 1,
                                p,
                                p / 3,
                                p / 7,
                                q,
                                q,
                                q,
                                math.cos(angle),
                                1,
                            ]
                        )
                    await ws.send_json({"seq": 8, "client_ms": 1001, "bones": bones})
                    a, b = await asyncio.wait_for(
                        asyncio.gather(
                            loop.sock_recv(observer, 65535),
                            loop.sock_recv(unreal, 65535),
                        ),
                        2,
                    )
                    self.assertGreater(len(a), 9216)
                    self.assertEqual(a, b)
                    frame = json.loads(a)
                    self.assertEqual(frame["bones"], bones)
                    self.assertEqual(frame["seq"], "8")
                    self.assertLess(
                        abs(int(frame["station_ns"]) - time.monotonic_ns()), 100_000_000
                    )

    async def test_remote_editor_subject_and_clock_are_assigned_at_station(self):
        from quest_bridge import editor_listener

        loop = asyncio.get_running_loop()
        with (
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sim,
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as hub,
        ):
            for udp in (sim, hub):
                udp.bind(("127.0.0.1", 0))
                udp.setblocking(False)
            ports = (hub.getsockname()[1], sim.getsockname()[1])
            listener = await editor_listener("127.0.0.1", "127.0.0.1", 0, ports, None)
            async with listener:
                reader, writer = await asyncio.open_connection(
                    "127.0.0.1", listener.sockets[0].getsockname()[1]
                )
                ping = json.loads(await asyncio.wait_for(reader.readline(), 2))
                frame = {
                    "seq": 1,
                    "client_ms": 72001,
                    "subject": "Human_Quest_A",
                    "bones": [
                        ["hips", -1, 0, 1, 0, 0, 0, 0, 1, 0.45],
                        ["head", 0, 0, 1.7, 0, 0, 0, 0, 1, 1],
                    ],
                }
                for message in ({"pong": ping["ping"], "client_ms": 72000}, frame):
                    writer.write(json.dumps(message).encode() + b"\n")
                await writer.drain()
                first, second = await asyncio.wait_for(
                    asyncio.gather(
                        loop.sock_recv(hub, 65535), loop.sock_recv(sim, 65535)
                    ),
                    2,
                )
                self.assertEqual(first, second)
                data = json.loads(first)
                self.assertEqual(
                    (data["subject"], data["timing"], data["space"]),
                    ("Human_Quest_C", "editor_xr_sample", "quest_local_floor"),
                )
                self.assertLess(
                    abs(int(data["station_ns"]) - time.monotonic_ns()), 100_000_000
                )
                writer.close()
                await writer.wait_closed()
