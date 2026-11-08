#!/usr/bin/env python3
"""Inventory Linux webcams or save one bounded diagnostic snapshot, without models.

python3 arm/tools/camera_inspect_linux.py inventory
python3 arm/tools/camera_inspect_linux.py snapshot \
    --device /dev/v4l/by-id/DEVICE-video-index0 --output NEW_DIRECTORY

Requires GStreamer's v4l2src, videoconvert, jpegenc and multifilesink plugins.
Defaults: 640x480 at 30 fps, sample 30 frames to allow exposure to settle and
retain only the last. Output uses the Mac helper's JPEG/metadata names, but is
not a running camera feed: the arm freshness guard will expire it. Timing is
host receipt timing, not validated sensor exposure. No arm modules are loaded.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import time


def inventory():
    """Read sysfs and stable aliases without opening any camera."""
    devices = []
    aliases = sorted(Path('/dev/v4l/by-id').glob('*'))
    aliases += sorted(Path('/dev/v4l/by-path').glob('*'))
    for node in sorted(Path('/sys/class/video4linux').glob('video*')):
        device = Path('/dev') / node.name
        devices.append({
            'device': str(device), 'name': (node / 'name').read_text().strip(),
            'index': (node / 'index').read_text().strip(),
            'stable_aliases': [str(p) for p in aliases if p.resolve() == device],
            'read_write_access': os.access(device, os.R_OK | os.W_OK),
        })
    return {'devices': devices, 'gst_launch': shutil.which('gst-launch-1.0'),
            'note': 'Nodes can include metadata endpoints; capture capability is not yet verified.'}


def inspect_device(device):
    if device.parent not in (Path('/dev/v4l/by-id'), Path('/dev/v4l/by-path')):
        raise ValueError('Select an explicit /dev/v4l/by-id/ or /dev/v4l/by-path/ alias from inventory.')
    resolved = device.resolve(strict=True)
    if (not device.is_symlink() or resolved.parent != Path('/dev')
            or not resolved.name.startswith('video') or not stat.S_ISCHR(resolved.stat().st_mode)):
        raise ValueError('Selected alias does not resolve to a video character device.')
    node = Path('/sys/class/video4linux') / resolved.name
    return {'unique_id': device.name, 'device': str(device), 'resolved_device': str(resolved),
            'name': (node / 'name').read_text().strip()}


def jpeg_dimensions(path):
    """Check JPEG framing and read its SOF dimensions; not a full image decoder."""
    if not 4 <= path.stat().st_size <= 10 * 1024 * 1024:
        raise ValueError('Missing or oversized JPEG.')
    data = path.read_bytes()
    if not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
        raise ValueError('Incomplete JPEG.')
    position = 2
    while position < len(data) - 2:
        if data[position] != 0xff:
            break
        while position < len(data) and data[position] == 0xff:
            position += 1
        marker = data[position]
        position += 1
        if marker in (0xda, 0xd9):
            break
        if position + 2 > len(data):
            break
        size = int.from_bytes(data[position:position + 2], 'big')
        if size < 2 or position + size > len(data):
            break
        if marker in (0xc0, 0xc1, 0xc2):
            if size < 8:
                break
            height = int.from_bytes(data[position + 3:position + 5], 'big')
            width = int.from_bytes(data[position + 5:position + 7], 'big')
            return width, height
        position += size
    raise ValueError('No valid JPEG dimensions.')


def write_json(path, value):
    temporary = path.with_name('.' + path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def snapshot(device, output, frames=30, fps=30, timeout=15):
    if not 1 <= frames <= 120 or not 1 <= fps <= 60 or not 1 <= timeout <= 30:
        raise ValueError('Require 1–120 frames, 1–60 fps and a 1–30 second timeout.')
    identity = inspect_device(device)
    executable = shutil.which('gst-launch-1.0')
    if not executable:
        raise RuntimeError('gst-launch-1.0 is missing; no packages were installed.')
    output = output.absolute()
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    image = output / ('camera-' + device.name + '.jpg')
    report = {'when': datetime.now(timezone.utc).isoformat(), 'selection': str(device),
              'requested_size': '640x480', 'sampled_frames': frames, 'requested_fps': fps,
              'continuous_capture': False, 'cameras': []}
    try:
        with tempfile.TemporaryDirectory(prefix='.capture-', dir=output) as staging:
            pattern = str(Path(staging) / 'frame-%03d.jpg')
            command = [executable, '-q', '-e', 'v4l2src',
                       'device=' + json.dumps(str(device)), 'num-buffers=' + str(frames),
                       '!', f'video/x-raw,width=640,height=480,framerate={fps}/1',
                       '!', 'videoconvert', '!', 'jpegenc', '!', 'multifilesink',
                       'location=' + json.dumps(pattern), 'max-files=1']
            started = time.monotonic()
            result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.PIPE, timeout=timeout, check=False)
            received = time.monotonic()
            if result.returncode:
                raise RuntimeError('GStreamer failed: ' + result.stderr.decode(errors='replace')[-4000:])
            retained = Path(staging) / f'frame-{frames - 1:03d}.jpg'
            if jpeg_dimensions(retained) != (640, 480):
                raise ValueError('Captured JPEG dimensions differ from 640x480.')
            if inspect_device(device) != identity:
                raise RuntimeError('Camera identity changed during capture.')
            retained.replace(image)
        capture = {'image': str(image), 'width': 640, 'height': 480,
                   'frames_received': frames, 'receipt_uptime_s': received,
                   'capture_started_uptime_s': started,
                   'timing_note': 'Host completion of bounded GStreamer capture; sensor exposure timing unvalidated'}
        report['cameras'] = [{**identity, 'capture': capture, 'saved_frames': [capture]}]
        write_json(output / 'camera-report.json', report)
        write_json(output / 'capture-latest.json', capture)
        # Publish readiness last, only after the image and its metadata are complete.
        write_json(output / 'capture-ready.txt', capture)
        return report
    except BaseException as error:
        # Only this newly created directory is touched. Never leave partial success markers.
        for name in ('capture-ready.txt', 'capture-latest.json',
                     '.capture-ready.txt.tmp', '.capture-latest.json.tmp'):
            (output / name).unlink(missing_ok=True)
        image.unlink(missing_ok=True)
        report.update(error=f'{type(error).__name__}: {error}', cameras=[])
        write_json(output / 'camera-report.json', report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    actions = parser.add_subparsers(dest='action', required=True)
    actions.add_parser('inventory')
    capture = actions.add_parser('snapshot')
    capture.add_argument('--device', type=Path, required=True)
    capture.add_argument('--output', type=Path, required=True, help='New directory beneath an existing parent')
    capture.add_argument('--frames', type=int, default=30, help='Sample this many frames; retain the last only')
    capture.add_argument('--fps', type=int, default=30)
    capture.add_argument('--timeout-seconds', type=float, default=15)
    args = parser.parse_args()
    if platform.system() != 'Linux':
        parser.error('Linux is required; use camera_inspect on macOS.')
    try:
        result = inventory() if args.action == 'inventory' else snapshot(
            args.device, args.output, args.frames, args.fps, args.timeout_seconds)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f'Camera inspection failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
