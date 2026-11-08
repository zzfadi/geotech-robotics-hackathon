# /// script
# requires-python = ">=3.11,<3.14"
# dependencies = ["numpy>=1.26,<3", "scipy>=1.12,<2", "opencv-python-headless>=4.9,<5", "psutil>=6"]
# ///
"""Operator side of the Quest/OXRSys look-to-relock path. No headset images are stored."""

import argparse
import json
import math
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
from collections import deque
from contextlib import contextmanager
from pathlib import Path

import cv2
import numpy as np
import psutil
from plate_vision import (
    Ambiguous,
    Hold,
    Observation,
    calibrate,
    estimate,
    plates,
    room_fields,
    room_from_state,
    ue_pose,
)
from scipy.spatial.transform import Rotation
from silver_screws import match_screws

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = ROOT / "Unreal/TheHumanTouch/Saved/Capture/Relock"


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode="w") as out:
        tmp = Path(out.name)
        try:
            json.dump(value, out, allow_nan=False)
        except (TypeError, ValueError, OSError):
            out.close()
            tmp.unlink(missing_ok=True)
            raise
    try:
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def adb_program():
    sdk = Path(os.environ.get("ANDROID_HOME", str(Path.home() / "Library/Android/sdk")))
    return shutil.which("adb") or str(sdk / "platform-tools/adb")


@contextmanager
def forward(serial, open_camera=False):
    adb = adb_program()
    if serial is None:
        rows = subprocess.check_output([adb, "devices"], text=True).splitlines()[1:]
        devices = [r.split()[0] for r in rows if r.split()[1:2] == ["device"]]
        if len(devices) != 1:
            raise Hold("select one authorized Quest with --serial")
        serial = devices[0]
    cmd = [adb, "-s", serial]
    if open_camera:
        subprocess.run(
            [
                *cmd,
                "shell",
                "am",
                "start",
                "-n",
                "net.demonixis.oxrsys.android/com.oculus.NativeActivity",
                "--ez",
                "oxrsys_relock",
                "true",
            ],
            check=True,
            timeout=8,
            capture_output=True,
        )
    # Wireless ADB may give the same headset a different transport address.
    hardware = subprocess.check_output(
        [*cmd, "shell", "getprop", "ro.serialno"], text=True, timeout=8
    ).strip()
    device_id = hardware if hardware and hardware.lower() != "unknown" else serial
    port = int(
        subprocess.check_output(
            [*cmd, "forward", "tcp:0", "localabstract:oxrsys_station_camera_v1"],
            text=True,
            timeout=8,
        )
    )
    try:
        yield device_id, port
    finally:
        subprocess.run(
            [*cmd, "forward", "--remove", f"tcp:{port}"],
            capture_output=True,
            timeout=8,
            check=False,
        )


def read_frame(port):
    with socket.create_connection(("127.0.0.1", port), 2) as sock:
        sock.settimeout(2.5)
        sock.sendall(b"\x01")
        with sock.makefile("rb") as stream:
            header = stream.read(4)
            if len(header) != 4:
                raise Hold(
                    "no camera endpoint; start the relock-enabled OXRSys activity and grant camera access"
                )
            (n,) = struct.unpack("!I", header)
            if not 2 <= n <= 4096:
                raise Hold("invalid camera header")
            meta = json.loads(stream.read(n))
            if "error" in meta:
                raise Hold(str(meta["error"]))
            if (meta.get("version"), meta.get("width"), meta.get("height")) != (
                1,
                1280,
                960,
            ):
                raise Hold("unsupported camera stream format")
            data = stream.read(1280 * 960 * 3 // 2)
            if len(data) != 1280 * 960 * 3 // 2:
                raise Hold("incomplete exposure")
    stamp = meta.pop("timestamp_ns", None)
    if not isinstance(stamp, int) or stamp <= 0:
        raise Hold("missing sensor timestamp")
    frame = cv2.cvtColor(
        np.frombuffer(data, np.uint8).reshape(1440, 1280), cv2.COLOR_YUV2BGR_I420
    )
    return frame, meta, stamp


def state(path):
    try:
        s = json.loads(path.read_text())
        age = time.time() - s["utc"]
        if not -0.05 <= age < 0.6 or not s["epoch"]:
            raise Hold("editor pose stream is stale")
        return s
    except (OSError, KeyError, json.JSONDecodeError) as err:
        raise Hold("waiting for a live seed and HumanTouch.Relock on") from err


def track_pose(samples, start, end):
    """A request-tagged sensor exposure must be inside this quiet host interval.
    The guards cover streaming prediction and delivery; no cross-clock subtraction."""
    rows = np.asarray(samples, float)
    if (
        rows.ndim != 2
        or rows.shape[1] != 8
        or len(rows) < 4
        or not np.isfinite(rows).all()
    ):
        raise Hold("missing tracking history")
    times = rows[:, 0]
    i = np.searchsorted(times, start, side="right") - 1
    j = np.searchsorted(times, end)
    if (
        i < 0
        or j >= len(rows)
        or end - start > 1.7
        or end <= start
        or np.any(np.diff(times) <= 0)
    ):
        raise Hold("exposure not covered by pose history")
    seq = rows[i : j + 1]
    if len(seq) < 4 or np.max(np.diff(seq[:, 0])) > 0.11:
        raise Hold("tracking gap during exposure")
    k = len(seq) // 2
    rot = Rotation.from_quat(seq[:, 4:])
    shift = np.linalg.norm(seq[:, 1:4] - seq[k, 1:4], axis=1)
    if max(shift) > 0.22 or max((rot[k].inv() * rot).magnitude()) > math.radians(0.35):
        raise Hold("hold still on the plate for a moment")
    return ue_pose(seq[k, 1:4], rot[k].as_matrix())


def context_id(s):
    return (s["epoch"], s["revision"], s["table"])


def capture(port, path, profile=None, serial=None):
    before = state(path)
    start = time.time() - 0.14
    image, meta, stamp = read_frame(port)
    end = time.time() + 0.14
    # Wait for the editor's post-exposure samples. Never pair old camera pixels
    # with the headset pose at the time the image finished crossing ADB.
    deadline = time.monotonic() + 0.8
    while True:
        after = state(path)
        if context_id(after) != context_id(before):
            raise Hold("registration changed during exposure")
        if after["poses"] and after["poses"][-1][0] >= end:
            break
        if time.monotonic() > deadline:
            raise Hold("post-exposure tracking unavailable")
        time.sleep(0.025)
    head = track_pose(after["poses"], start, end)
    optics = profile_data(profile, serial, meta) if profile is not None else None
    matches = match_screws(
        image, room_from_state(after), head, plates(after["marks"]), optics
    )
    sightings = [
        Observation(side, uv, head, end - 0.14, stamp) for side, uv, _, _ in matches
    ]
    return after, meta, sightings, matches, optics


def profile_data(path, serial, meta):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise Hold("enroll this camera profile first") from e
    if (
        data.get("version") != 1
        or data.get("serial") != serial
        or data.get("camera") != meta
    ):
        raise Hold("device, OS or camera mode differs from enrollment")
    k, d, lens = [
        np.asarray(data.get(key), float) for key in ("K", "D", "head_from_camera")
    ]
    if (
        k.shape != (3, 3)
        or d.shape not in ((1, 5), (5,))
        or lens.shape != (4, 4)
        or not all(np.isfinite(x).all() for x in (k, d, lens))
    ):
        raise Hold("invalid camera profile")
    r = lens[:3, :3]
    if (
        not np.allclose(lens[3], [0, 0, 0, 1])
        or not np.allclose(r.T @ r, np.eye(3), atol=1e-4)
        or np.linalg.det(r) < 0.999
        or np.linalg.norm(lens[:3, 3]) > 0.20
        or min(k[0, 0], k[1, 1]) <= 0
    ):
        raise Hold("invalid rigid camera calibration")
    return k, d, lens


def run(args, editor=None, deadline=None):
    source = args.dir / "state.json"
    profile = args.dir / ("lens-" + args.profile + ".json")
    seen = deque(maxlen=100)
    observations = []
    previous = None
    camera = None
    last_message = ""
    last_print = 0.0

    def report(message):
        nonlocal last_message, last_print
        if message != last_message or time.monotonic() - last_print > 4:
            print(message, flush=True)
            last_message = message
            last_print = time.monotonic()

    with forward(args.serial, open_camera=editor is not None) as (serial, port):
        print(
            f"{args.mode}: paired OXRSys camera; {'automatic bounded correction' if args.apply else 'no room writes'}",
            flush=True,
        )
        end = deadline if deadline is not None else time.monotonic() + args.seconds
        while time.monotonic() < end:
            if editor and not editor.is_running():
                return
            try:
                if args.mode == "watch" and not profile.is_file():
                    raise Hold("waiting for a qualified lens profile; run enroll once on this headset")
                s, meta, sightings, matches, optics = capture(
                    port, source, profile if args.mode == "watch" else None, serial
                )
                if editor and s.get("editor_pid") != editor.pid:
                    raise Hold("waiting for the launched editor")
                if args.apply and not s.get("allow_correction"):
                    raise Hold("editor is in initial-calibration or observe-only mode")
                stamp = sightings[0].sensor_ns
                identity = context_id(s)
                if camera != meta or previous != identity:
                    observations = []
                    seen.clear()
                    camera = meta
                    previous = identity
                if args.mode == "contrast":
                    for side, _, contrast, minor in matches:
                        report(
                            f"{side} PASS: six silver screws, minimum rim contrast {contrast:.0f}/255, minor diameter {minor:.1f}px"
                        )
                        seen.append(side)
                    if {"left", "right"} <= set(seen):
                        return
                    continue
                if stamp in seen:
                    raise Hold("duplicate exposure")
                seen.append(stamp)
                layout = plates(s["marks"])
                room = room_from_state(s)
                if args.mode == "enroll":
                    previous_count = len(observations)
                    for o in sightings:
                        same = [x for x in observations if x.side == o.side]
                        if len(same) >= 12:
                            continue
                        if any(
                            np.linalg.norm(x.head[:3, 3] - o.head[:3, 3]) < 0.035
                            and Rotation.from_matrix(
                                x.head[:3, :3].T @ o.head[:3, :3]
                            ).magnitude()
                            < math.radians(7)
                            for x in same
                        ):
                            continue
                        observations.append(o)
                    if len(observations) == previous_count:
                        raise Hold(
                            "change viewpoint or look at the other base, then hold still"
                        )
                    counts = [
                        sum(x.side == side for x in observations) for side in layout
                    ]
                    report(f"enrolled views left/right {counts}")
                    if min(counts) < 8:
                        continue
                    try:
                        k, d, lens, quality = calibrate(
                            observations,
                            layout,
                            room,
                            (1280, 960),
                            dock_seed=s["seed"] == "dock",
                        )
                    except Hold:
                        if min(counts) >= 12:
                            for side in layout:
                                del observations[
                                    next(
                                        i
                                        for i, x in enumerate(observations)
                                        if x.side == side
                                    )
                                ]
                        raise
                    atomic(
                        profile,
                        {
                            "version": 1,
                            "serial": serial,
                            "camera": meta,
                            "seed": s["seed"],
                            "K": k.tolist(),
                            "D": d.tolist(),
                            "head_from_camera": lens.tolist(),
                            "quality": quality,
                        },
                    )
                    print("Qualified camera profile", profile, quality)
                    return
                else:
                    k, d, lens = optics
                    observations = [
                        x for x in observations if sightings[0].stamp - x.stamp < 11.5
                    ]
                    observations.extend(sightings)
                    # Bound CPU and retain independent recent viewpoints on both bases.
                    observations = sorted(
                        [
                            x
                            for side in layout
                            for x in [v for v in observations if v.side == side][-8:]
                        ],
                        key=lambda x: x.stamp,
                    )
                    m, quality = estimate(
                        observations, layout, room, k, d, lens, time.time()
                    )
                    if context_id(state(source)) != identity:
                        raise Hold("estimate superseded")
                    if args.apply:
                        atomic(
                            args.dir / "candidate.json",
                            dict(
                                **room_fields(m),
                                epoch=s["epoch"],
                                revision=s["revision"],
                                utc=time.time(),
                                left_utc=max(
                                    v.stamp for v in observations if v.side == "left"
                                ),
                                right_utc=max(
                                    v.stamp for v in observations if v.side == "right"
                                ),
                            ),
                        )
                    report(
                        ("Submitted" if args.apply else "Observed")
                        + f" two-base correction {quality}"
                    )
                    observations.clear()
            except ConnectionRefusedError:
                if editor:
                    raise
                report("HOLD: camera connection refused")
            except Ambiguous as e:
                if args.mode == "watch":
                    observations.clear()
                report("HOLD: " + str(e))
            except (OSError, ValueError, cv2.error, np.linalg.LinAlgError) as e:
                report("HOLD: " + str(e))
            finally:
                time.sleep(0.12)
    if args.mode != "watch":
        raise Hold("time expired without qualifying both bases")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=("contrast", "enroll", "watch"))
    p.add_argument(
        "--profile", default="quest3", help="local headset/lens profile name"
    )
    p.add_argument("--serial", help="authorized ADB device (USB or paired wireless)")
    p.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    p.add_argument("--seconds", type=float, default=600)
    p.add_argument(
        "--drift-correction",
        "--apply",
        dest="apply",
        action="store_true",
        help="apply accepted two-base corrections in watch mode",
    )
    p.add_argument("--editor-pid", type=int, help=argparse.SUPPRESS)
    a = p.parse_args()
    if (
        not a.profile.replace("-", "").replace("_", "").isalnum()
        or a.seconds <= 0
        or not math.isfinite(a.seconds)
        or (a.apply and a.mode != "watch")
    ):
        p.error("invalid profile, duration or apply mode")
    try:
        if a.editor_pid:
            editor = psutil.Process(a.editor_pid)
            deadline = time.monotonic() + a.seconds
            while editor.is_running() and time.monotonic() < deadline:
                try:
                    run(a, editor, deadline)
                    break
                except (Hold, OSError, subprocess.SubprocessError) as err:
                    print(f"Waiting for the paired Quest: {err}", flush=True)
                    time.sleep(3)
        else:
            run(a)
    except KeyboardInterrupt:
        pass
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        sys.exit(str(e))
