"""Receive the local simulation copy of pose frames. No motor or robot connection."""

import argparse
import json
import socket
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=17861)
    parser.add_argument(
        "--frames", type=int, default=0, help="0 listens until interrupted"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="Seconds to wait for the next packet",
    )
    parser.add_argument(
        "--jsonl",
        action="store_true",
        help="Print full skeletons instead of a frame summary",
    )
    args = parser.parse_args()
    if not 0 < args.port < 65536 or args.frames < 0:
        parser.error("Invalid port or frame count")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind(("127.0.0.1", args.port))
        udp.settimeout(args.timeout)
        received = 0
        while not args.frames or received < args.frames:
            try:
                packet, _ = udp.recvfrom(65535)
            except TimeoutError:
                raise SystemExit("Timed out waiting for a pose frame")
            now = time.monotonic_ns()
            frame = json.loads(packet)
            if args.jsonl:
                print(packet.decode(), flush=True)
            else:
                age = (now - int(frame["station_ns"])) / 1e6
                print(
                    f"{frame['subject']} session={frame['session']} seq={frame['seq']} "
                    f"station_ns={frame['station_ns']} age_ms={age:.1f} "
                    f"bones={len(frame['bones'])} space={frame['space']}",
                    flush=True,
                )
            received += 1


if __name__ == "__main__":
    main()
