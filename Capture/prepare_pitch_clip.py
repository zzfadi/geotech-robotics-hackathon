"""Trim a single-eye Quest recording into a silent, offline 16:9 pitch MP4."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile


def inspect(path):
    result = subprocess.check_output([
        "ffprobe", "-v", "error", "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height,pix_fmt",
        "-of", "json", str(path),
    ], text=True)
    return json.loads(result)


def prepare(source, destination, start, seconds):
    if destination.suffix.lower() != ".mp4" or destination.exists():
        raise ValueError("Choose a new .mp4 output filename")
    if not all(math.isfinite(v) for v in (start, seconds)) or start < 0 or seconds <= 0:
        raise ValueError("Start must be nonnegative and duration must be positive")
    original = inspect(source)
    if not any(s["codec_type"] == "video" for s in original["streams"]):
        raise ValueError("The input has no video stream")
    if start + seconds > float(original["format"]["duration"]) + 0.05:
        raise ValueError("The requested shot extends beyond the recording")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temp:
        clip = Path(temp) / "pitch.mp4"
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-ss", str(start), "-i", str(source),
            "-t", str(seconds), "-map", "0:v:0", "-an", "-vf",
            "fps=30,scale=1920:1080:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1", "-c:v", "libx264", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(clip),
        ], check=True)
        result = inspect(clip)
        stream = result["streams"][0]
        if (len(result["streams"]) != 1 or stream["codec_name"] != "h264"
                or (stream["width"], stream["height"]) != (1920, 1080)
                or stream["pix_fmt"] != "yuv420p" or abs(float(result["format"]["duration"]) - seconds) > .15):
            raise ValueError("The exported clip failed its playback format or duration check")
        os.link(clip, destination)
    print(f"{destination}  {seconds:g}s, 1920 × 1080, H.264, silent")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--start", type=float, default=0, help="first second to keep")
    parser.add_argument("--seconds", type=float, required=True, help="shot duration")
    args = parser.parse_args()
    try:
        prepare(args.source.resolve(), args.destination.resolve(), args.start, args.seconds)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"{error}\n")
