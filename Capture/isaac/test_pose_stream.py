import asyncio
import json
import socket
import time
import unittest

from pose_stream import MAX_AGE, SUBJECTS, Gateway, PoseReceiver, Samples, is_pose


def pose(name, stamp, seq=1, space="table_v1"):
    return {
        "type": "hub_evaluated_pose_v2",
        "subject": name,
        "evaluation": {"session": "run", "tick": str(seq), "station_ns": str(stamp)},
        "capture_ref": {
            "session": "fixture",
            "seq": str(seq),
            "station_ns": str(stamp - 1),
        },
        "space": space,
        "bones": [
            ["hips", -1, 0, 0, 1, 0, 0, 0, 1, 1],
            ["head", 0, 0, 0, 1.72, 0, 0, 0, 1, 1],
        ],
    }


class ClockTest(unittest.TestCase):
    def test_offset_different_clock_epochs_reorder_playout_and_expiry(self):
        samples = Samples()
        start = 1_000_000_000
        offset = 70_000_000_000
        rtt = 46_000_000
        samples.sample_clock(
            start, start + offset + rtt // 2, start + offset + rtt // 2, start + rtt
        )
        now = start + 200_000_000
        station_now = now + offset
        frames = [
            pose(SUBJECTS[0], station_now - 110_000_000, 10),
            pose(SUBJECTS[0], station_now - 80_000_000, 11),
            pose(SUBJECTS[0], station_now - 140_000_000, 9),
            pose(SUBJECTS[1], station_now - 120_000_000, 7, "hip_relative"),
        ]
        for frame in frames:
            samples.store(frame)
        sample = samples.select(now, 100_000_000, "table_v1")
        self.assertEqual(sample["rtt_ms"], 46)
        self.assertEqual(sample["target_ns"], station_now - 100_000_000)
        self.assertEqual(sample["poses"], {SUBJECTS[0]: frames[0]})
        self.assertEqual(
            samples.select(now + MAX_AGE, 100_000_000, "table_v1")["poses"], {}
        )
        self.assertIsNone(samples.select(now + 5_000_000_000, 0, None)["target_ns"])
        broken = pose(SUBJECTS[0], station_now)
        broken["bones"][1][1] = 1
        self.assertFalse(is_pose(broken))


class WireTest(unittest.IsolatedAsyncioTestCase):
    async def test_three_subjects_over_udp_export_tcp_and_receiver_then_disconnect(
        self,
    ):
        loop = asyncio.get_running_loop()
        g = Gateway("127.0.0.1")
        udp, _ = await loop.create_datagram_endpoint(
            lambda: g, local_addr=("127.0.0.1", 0)
        )
        server = await asyncio.start_server(g.accept, "127.0.0.1", 0, limit=256)
        r = PoseReceiver("127.0.0.1", server.sockets[0].getsockname()[1], delay_ms=25)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as source:
                source.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65535)
                for i in range(100):
                    now = time.monotonic_ns()
                    for subject in SUBJECTS:
                        frame = pose(subject, now, i)
                        count = 23 if subject == "Human_Cam_B" else 67
                        for j in range(2, count):
                            frame["bones"].append(
                                [
                                    f"articulated_joint_{j}",
                                    j - 1,
                                    j / 67,
                                    j / 87,
                                    j / 113,
                                    0,
                                    0,
                                    0,
                                    1,
                                    0.99,
                                ]
                            )
                        source.sendto(
                            json.dumps(frame).encode(),
                            udp.get_extra_info("sockname"),
                        )
                    await asyncio.sleep(0.01)
                    sample = r.before_step("table_v1")
                    if set(sample["poses"]) == set(SUBJECTS):
                        break
                self.assertEqual(set(sample["poses"]), set(SUBJECTS))
                self.assertEqual(
                    [len(sample["poses"][s]["bones"]) for s in SUBJECTS], [67, 23, 67]
                )
                self.assertEqual(
                    len(
                        {
                            v["evaluation"]["station_ns"]
                            for v in sample["poses"].values()
                        }
                    ),
                    1,
                )
                self.assertLessEqual(
                    int(sample["poses"][SUBJECTS[0]]["evaluation"]["station_ns"]),
                    sample["target_ns"],
                )
            await asyncio.sleep(0.30)
            self.assertFalse(r.before_step()["poses"])
            # A receiver whose TCP connection drops must invalidate its clock and all poses.
            g.client.close()
            await asyncio.sleep(0.1)
            self.assertIsNone(r.before_step()["target_ns"])
        finally:
            await asyncio.to_thread(r.close)
            server.close()
            await server.wait_closed()
            udp.close()
