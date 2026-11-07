"""Export evaluated station Hub poses to an authorized Tailscale client.

Run 'serve' on the same Mac as the capture station clock and Hub client editor.
Run 'observe' on Luna2, or use PoseReceiver from an Isaac physics callback.
"""

import argparse
import asyncio
import contextlib
import ipaddress
import json
import math
import threading
import time
from collections import deque

SUBJECTS = ("Human_Quest_A", "Human_Cam_B", "Human_Quest_C")
TAILNET = ipaddress.ip_network("100.64.0.0/10")
MAX_AGE = 250_000_000
LIMIT = 60_000


def is_pose(frame):
    if (
        not isinstance(frame, dict)
        or frame.get("type") != "hub_evaluated_pose_v2"
        or frame.get("subject") not in SUBJECTS
    ):
        return False
    if not isinstance(frame.get("space"), str) or not 0 < len(frame["space"]) <= 128:
        return False
    for field, sequence in (("evaluation", "tick"), ("capture_ref", "seq")):
        identity = frame.get(field)
        if (
            not isinstance(identity, dict)
            or not isinstance(identity.get("session"), str)
            or not 0 < len(identity["session"]) <= 128
        ):
            return False
        for key in (sequence, "station_ns"):
            value = identity.get(key)
            if (
                not isinstance(value, str)
                or not 0 < len(value) <= 20
                or not value.isascii()
                or not value.isdecimal()
            ):
                return False
    bones = frame.get("bones")
    if not isinstance(bones, list) or not 1 <= len(bones) <= 100:
        return False
    names = set()
    for i, row in enumerate(bones):
        if not isinstance(row, list) or len(row) != 10:
            return False
        name, parent, *values = row
        if (
            not isinstance(name, str)
            or not 0 < len(name) <= 128
            or name in names
            or type(parent) is not int
            or (parent != -1 if i == 0 else not 0 <= parent < i)
        ):
            return False
        names.add(name)
        if not all(type(v) in (int, float) and abs(v) < 1e6 for v in values):
            return False
        if (
            not 0 <= values[-1] <= 1
            or not 0.98 <= sum(v * v for v in values[3:7]) <= 1.02
        ):
            return False
    return True


def fresh(frame, station_now):
    # A held subject may be read on a new editor tick, even after its sensor disconnected.
    return all(
        -50_000_000 <= station_now - int(frame[key]["station_ns"]) <= MAX_AGE
        for key in ("evaluation", "capture_ref")
    )


class Gateway(asyncio.DatagramProtocol):
    def __init__(self, peer):
        self.peer = peer
        self.latest = {}
        self.client = None
        self.changed = asyncio.Event()

    def datagram_received(self, raw, _address):
        if len(raw) > LIMIT or b"\n" in raw:
            return
        try:
            frame = json.loads(raw)
            if not is_pose(frame):
                return
            stamp = int(frame["evaluation"]["station_ns"])
            if not fresh(frame, time.monotonic_ns()):
                return
            name = frame["subject"]
            if name in self.latest and stamp <= self.latest[name][0]:
                return
            self.latest[name] = (stamp, raw)
            self.changed.set()
        except (UnicodeError, ValueError, OverflowError, RecursionError):
            return

    async def accept(self, reader, writer):
        if writer.get_extra_info("peername")[0] != self.peer or self.client is not None:
            writer.close()
            await writer.wait_closed()
            return
        self.client = writer
        sender = asyncio.create_task(self.send_poses(writer))
        try:
            while line := await asyncio.wait_for(reader.readline(), 10):
                received = time.monotonic_ns()
                # Echo the original reader timestamp. Station receive and send timestamps
                # let the reader subtract the time spent processing the request.
                ping = json.loads(line)
                sent = ping.get("ping") if isinstance(ping, dict) else None
                if type(sent) is not int or sent < 0:
                    break
                writer.write(
                    json.dumps(
                        {"pong": sent, "s2": received, "s3": time.monotonic_ns()}
                    ).encode()
                    + b"\n"
                )
                await asyncio.wait_for(writer.drain(), 1)
        except (OSError, ValueError, RecursionError, TimeoutError):
            pass
        finally:
            sender.cancel()
            with contextlib.suppress(asyncio.CancelledError, OSError, TimeoutError):
                await sender
            self.client = None
            writer.close()
            with contextlib.suppress(OSError):
                await writer.wait_closed()

    async def send_poses(self, writer):
        sent = {}
        while True:
            self.changed.clear()
            now = time.monotonic_ns()
            # Coalesce independently per subject if the WAN client falls behind.
            for name, (stamp, body) in self.latest.items():
                if stamp != sent.get(name) and now - stamp <= MAX_AGE:
                    writer.write(body + b"\n")
                    sent[name] = stamp
            try:
                await asyncio.wait_for(writer.drain(), 0.5)
            except (OSError, TimeoutError):
                writer.close()
                return
            await self.changed.wait()


async def serve(bind, peer, port, local_port):
    gateway = Gateway(peer)
    loop = asyncio.get_running_loop()
    udp, _ = await loop.create_datagram_endpoint(
        lambda: gateway, local_addr=("127.0.0.1", local_port)
    )
    try:
        async with await asyncio.start_server(
            gateway.accept, bind, port, limit=256
        ) as server:
            print(
                f"Hub export on loopback UDP {local_port}. Accepting {peer} on {bind}:{port}.",
                flush=True,
            )
            await server.serve_forever()
    finally:
        udp.close()


class Samples:
    """One timestamp target for every performer in a physics tick."""

    def __init__(self):
        self.frames = {name: deque(maxlen=128) for name in SUBJECTS}
        self.clocks = deque(maxlen=16)

    def sample_clock(self, t1, s2, s3, t4):
        rtt = (t4 - t1) - (s3 - s2)
        if s2 <= s3 and 0 <= rtt <= MAX_AGE:
            self.clocks.append((rtt, ((s2 - t1) + (s3 - t4)) // 2, t4))

    def store(self, frame):
        q = self.frames[frame["subject"]]
        stamp = int(frame["evaluation"]["station_ns"])
        if q and stamp <= q[-1][0]:
            return
        if q and any(
            frame[k]["session"] != q[-1][1][k]["session"]
            for k in ("evaluation", "capture_ref")
        ):
            q.clear()
        q.append((stamp, frame))

    def select(self, now, delay_ns, space):
        clocks = [c for c in self.clocks if now - c[2] < 4_000_000_000]
        if not clocks:
            return {"target_ns": None, "rtt_ms": None, "poses": {}}
        rtt, offset, _ = min(clocks)
        station_now = now + offset
        target = station_now - delay_ns
        poses = {}
        for name, q in self.frames.items():
            for stamp, frame in reversed(q):
                if stamp <= target:
                    if fresh(frame, station_now) and (
                        space is None or frame["space"] == space
                    ):
                        poses[name] = frame
                    break
        return {"target_ns": target, "rtt_ms": rtt / 1e6, "poses": poses}


class PoseReceiver:
    """Read the socket off the simulation thread. The caller owns USD/physics edits."""

    def __init__(self, station, port=17867, delay_ms=100):
        if not math.isfinite(delay_ms) or not 0 <= delay_ms < MAX_AGE / 1e6:
            raise ValueError("delay_ms must be in [0, 250)")
        self.station = station
        self.port = port
        self.delay_ns = int(delay_ms * 1e6)
        self.samples = Samples()
        self.lock = threading.Lock()
        self.closed = threading.Event()
        self.status = "Connecting"
        self.worker = threading.Thread(
            target=lambda: asyncio.run(self._run()), daemon=True
        )
        self.worker.start()

    def before_step(self, space=None):
        with self.lock:
            return self.samples.select(time.monotonic_ns(), self.delay_ns, space)

    def close(self):
        self.closed.set()
        self.worker.join(timeout=4)

    async def _run(self):
        while not self.closed.is_set():
            writer = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.station, self.port, limit=LIMIT + 1), 2
                )
                self.status = "Connected"
                outstanding = None
                last_ping = 0
                last_pong = time.monotonic_ns()
                while not self.closed.is_set():
                    now = time.monotonic_ns()
                    if now - last_pong > 4_000_000_000:
                        raise TimeoutError("station clock handshake expired")
                    if now - last_ping >= 500_000_000:
                        last_ping = outstanding = now
                        writer.write(json.dumps({"ping": now}).encode() + b"\n")
                        await asyncio.wait_for(writer.drain(), 0.5)
                    try:
                        line = await asyncio.wait_for(reader.readline(), 0.1)
                    except TimeoutError:
                        continue
                    arrived = time.monotonic_ns()
                    if not line:
                        raise ConnectionError("station closed connection")
                    packet = json.loads(line)
                    if not isinstance(packet, dict):
                        continue
                    if outstanding is not None and packet.get("pong") == outstanding:
                        s2, s3 = packet.get("s2"), packet.get("s3")
                        if type(s2) is int and type(s3) is int:
                            with self.lock:
                                self.samples.sample_clock(outstanding, s2, s3, arrived)
                            last_pong = arrived
                        outstanding = None
                    elif is_pose(packet):
                        with self.lock:
                            self.samples.store(packet)
            except (OSError, ValueError, RecursionError, TimeoutError) as error:
                self.status = str(error)
            finally:
                with self.lock:
                    self.samples = Samples()
                if writer:
                    writer.close()
                    with contextlib.suppress(OSError):
                        await writer.wait_closed()
            await asyncio.sleep(0.3)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="mode", required=True)
    gateway = commands.add_parser("serve")
    gateway.add_argument("--bind", required=True, type=ipaddress.IPv4Address)
    gateway.add_argument("--peer", required=True, type=ipaddress.IPv4Address)
    gateway.add_argument("--port", type=int, default=17867)
    gateway.add_argument("--local-port", type=int, default=17866)
    observe = commands.add_parser("observe")
    observe.add_argument("--station", required=True)
    observe.add_argument("--port", type=int, default=17867)
    observe.add_argument("--delay-ms", type=float, default=100)
    args = parser.parse_args()
    if args.mode == "serve":
        if any(
            ip not in TAILNET and not ip.is_loopback for ip in (args.bind, args.peer)
        ):
            parser.error("Supply Tailscale or loopback addresses")
        asyncio.run(serve(str(args.bind), str(args.peer), args.port, args.local_port))
    else:
        receiver = PoseReceiver(args.station, args.port, args.delay_ms)
        try:
            while True:
                sample = receiver.before_step()
                ids = {
                    k: {key: v[key] for key in ("evaluation", "capture_ref", "space")}
                    for k, v in sample["poses"].items()
                }
                print(receiver.status, sample["rtt_ms"], ids, flush=True)
                time.sleep(0.2)
        finally:
            receiver.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
