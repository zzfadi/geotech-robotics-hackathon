"""Audit a synthetic station-to-Isaac trial against the exact skeletons evaluated by Unreal."""

import argparse
import asyncio
import hashlib
import ipaddress
import json
import math
import socket
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rehearse_performers import skeleton
from pose_stream import Gateway, SUBJECTS, TAILNET

POSES = ("neutral", "reach", "curl", "squat", "lift")


def fingerprint(frame):
    # Include names, hierarchy, positions, orientations, confidence and both identities.
    return hashlib.sha256(
        json.dumps(
            frame, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def write(stream, record):
    stream.write(json.dumps(record, separators=(",", ":")) + "\n")


async def station(args):
    space = "spoof-" + args.trial
    observed = {}
    target_file = getattr(args, "contact_target", None)
    if target_file:
        from contact_fixture import SUBJECT as contact_subject, hand_sweep

    class AuditGateway(Gateway):
        def datagram_received(self, raw, address):
            super().datagram_received(raw, address)
            for name, (stamp, body) in self.latest.items():
                if observed.get(name) == stamp:
                    continue
                observed[name] = stamp
                frame = json.loads(body)
                if frame["space"] == space:
                    write(
                        log,
                        {
                            "subject": name,
                            "evaluation": frame["evaluation"],
                            "sha256": fingerprint(frame),
                        },
                    )

    with args.output.open("x", buffering=1) as log, socket.socket(
        socket.AF_INET, socket.SOCK_DGRAM
    ) as tx:
        tx.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65535)
        gateway = AuditGateway(args.luna)
        udp, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: gateway, local_addr=("127.0.0.1", 17866)
        )
        try:
            async with await asyncio.start_server(
                gateway.accept, args.bind, 17867, limit=256
            ):
                print("Ready for Luna", space, flush=True)
                contact_seq = 0
                center = None
                for seq in range(round(args.seconds * 30)):
                    # Keep the fixture idle after preflight until the allowed WAN client connects.
                    if seq >= 360 and gateway.client is None:
                        await asyncio.sleep(1 / 30)
                        continue
                    if target_file:
                        if center is None and target_file.is_file():
                            target = json.loads(target_file.read_text())
                            center = target["center_m"]
                            if (
                                target["trial"] != args.trial
                                or len(center) != 3
                                or not all(
                                    math.isfinite(v) and abs(v) < 100 for v in center
                                )
                            ):
                                raise ValueError(
                                    "Invalid Maker Arm target for this trial"
                                )
                        if center is None or gateway.client is None:
                            await asyncio.sleep(1 / 30)
                            continue
                    names = (contact_subject,) if center is not None else SUBJECTS
                    for name in names:
                        frame = dict(
                            version=1,
                            subject=name,
                            session=args.trial,
                            space=space,
                            seq=str(contact_seq if center is not None else seq),
                            station_ns=str(time.monotonic_ns()),
                            bones=(
                                hand_sweep(contact_seq, center)
                                if center is not None
                                else skeleton(name, POSES[(seq // 20) % len(POSES)])
                            ),
                        )
                        tx.sendto(
                            json.dumps(frame, separators=(",", ":")).encode(),
                            ("127.0.0.1", 17860),
                        )
                    contact_seq += 1
                    await asyncio.sleep(1 / 30)
        finally:
            udp.close()


def attach(context, stage, station_ip, trial, output, delay_ms=100):
    """Call from Luna's initialized Isaac simulation; call preview.close() at the end."""
    from isaac_preview import IsaacPreview

    seen = {}
    path = Path(output)
    path.touch(exist_ok=False)

    def record(tick, sample, artists):
        applied = time.monotonic_ns() + sample["clock_offset_ns"]
        with path.open("a") as log:
            for name, frame in sample["poses"].items():
                ev = frame["evaluation"]
                key = (ev["session"], ev["tick"])
                if seen.get(name) == key:
                    continue
                seen[name] = key
                points = artists.curves[name].GetPointsAttr().Get()
                bones = frame["bones"]
                expected = [
                    b[2:5]
                    for r in bones
                    if r[1] >= 0 and min(r[-1], bones[r[1]][-1]) >= 0.35
                    for b in (bones[r[1]], r)
                ]
                errors = [
                    math.dist(x, [float(v) * artists.metres_per_unit for v in y])
                    for x, y in zip(expected, points)
                ]
                write(
                    log,
                    {
                        "tick": tick,
                        "subject": name,
                        "evaluation": ev,
                        "capture_ref": frame["capture_ref"],
                        "sha256": fingerprint(frame),
                        "geometry_ok": len(points) == len(expected)
                        and bool(errors)
                        and max(errors) < 1e-5,
                        "evaluation_to_apply_ms": (applied - int(ev["station_ns"]))
                        / 1e6,
                        "capture_to_apply_ms": (
                            applied - int(frame["capture_ref"]["station_ns"])
                        )
                        / 1e6,
                        "clock_rtt_ms": sample["rtt_ms"],
                    },
                )

    return IsaacPreview(
        context,
        stage,
        station_ip,
        "spoof-" + trial,
        delay_ms=delay_ms,
        on_applied=record,
    )


def report(args):
    def rows(p):
        with p.open() as f:
            yield from (json.loads(line) for line in f if line.strip())

    def key(r):
        return r["subject"], r["evaluation"]["session"], r["evaluation"]["tick"]

    expected = {key(r): r["sha256"] for r in rows(args.unreal)}
    by_subject = {}
    ticks = {}
    mismatches = 0
    for r in rows(args.isaac):
        matches = expected.get(key(r)) == r["sha256"] and r["geometry_ok"]
        mismatches += not matches
        if matches:
            by_subject.setdefault(r["subject"], []).append(r)
            ticks.setdefault(r["tick"], set()).add(r["subject"])
    full = sum(len(names) == 3 for names in ticks.values())
    print(
        "Matched simultaneous three-subject first applications:",
        full,
        "mismatches:",
        mismatches,
    )
    for name, group in by_subject.items():
        print(name, len(group), "first applications")
        for metric in ("evaluation_to_apply_ms", "capture_to_apply_ms", "clock_rtt_ms"):
            vals = sorted(r[metric] for r in group)
            print(
                metric,
                "median",
                round(statistics.median(vals), 2),
                "p95",
                round(vals[math.ceil(0.95 * len(vals)) - 1], 2),
            )
    return (
        full > 0
        and not mismatches
        and all(len(group) >= 10 for group in by_subject.values())
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)
    p = sub.add_parser("station")
    p.add_argument(
        "--contact-target",
        type=Path,
        help="Wait for the live wrist center, then sweep the webcam hand into it",
    )
    p.add_argument("--seconds", type=int, default=1800)
    for opt in ("bind", "luna", "trial"):
        p.add_argument("--" + opt, required=True)
    p.add_argument("--output", type=Path, required=True)
    p = sub.add_parser("report")
    for opt in ("unreal", "isaac"):
        p.add_argument("--" + opt, type=Path, required=True)
    args = parser.parse_args()
    if args.mode == "report":
        sys.exit(0 if report(args) else 1)
    if any(
        (ip := ipaddress.ip_address(host)) not in TAILNET and not ip.is_loopback
        for host in (args.bind, args.luna)
    ):
        parser.error("Use Tailscale or loopback addresses")
    if args.seconds <= 0:
        parser.error("Trial duration must be positive")
    asyncio.run(station(args))
