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
