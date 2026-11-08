"""Keep real capture spacing in review clips instead of compressing wait/hold time."""
import json
import math
import re


def write_concat(folder, fallback_fps=2):
    manifest = folder/'overhead_timeline.jsonl'
    if not manifest.exists():
        return None
    frames = []
    for line in manifest.read_text().splitlines():
        item = json.loads(line)
        name, stamp = item['file'], item['monotonic']
        if not re.fullmatch(r'overhead_rec_\d{6}\.jpg', name):
            raise ValueError('Invalid recording frame name')
        if not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
            raise ValueError('Invalid recording timestamp')
        path = folder/name
        if not path.exists():
            continue
        data = path.read_bytes()
        if not (data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9')):
            continue
        if frames and stamp <= frames[-1][1]:
            raise ValueError('Recording timestamps must increase')
        frames.append((name, stamp))
    if not frames:
        return None
    lines = ['ffconcat version 1.0']
    for i, (name, stamp) in enumerate(frames):
        duration = frames[i+1][1]-stamp if i+1 < len(frames) else 1/fallback_fps
        lines.extend(["file '%s'" % name, 'duration %.9f' % duration])
    lines.append("file '%s'" % frames[-1][0])
    target = folder/'overhead.ffconcat'
    target.write_text('\n'.join(lines)+'\n')
    return target
