"""Videos the session hub (Tools/copresence_hub.py) serves to everyone in the session. An agent
brings a video in from its own machine with import_video (Tools/copresence_mcp.py), which
posts the file here. Multi-User only carries Unreal assets, so the video reaches the others as
a shared media source that streams it from the hub, which every editor and VR reach, whether
on the host's network or through the relay of the always-on session.

    POST /api/media?name=clip.mp4    the file as the body, with the session code from elsewhere
    GET  /api/media/<id>/<name>      the file, or the byte range a video player asks for

The videos are kept next to the hub's state file, under media/, until someone deletes them.
"""

import re
import secrets
import shutil
import tempfile
from pathlib import Path

MEDIA_PREFIX = "/api/media"
MEDIA_TYPES = {".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/x-m4v"}
MAX_MEDIA_BYTES = 200 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
TOKEN = re.compile(r"[0-9a-f]{16}")
RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


class MediaStore:
    def __init__(self, folder: Path | None):
        self.folder = folder or Path(tempfile.gettempdir()) / "copresence-hub-media"

    def receive(self, handler, name: str) -> tuple[int, dict]:
        """Keeps the video a request carries. Returns the status and reply for it, with the
        path the video is served at."""
        suffix = Path(name).suffix.lower()
        if suffix not in MEDIA_TYPES:
            return 400, {"error": f"videos are one of {', '.join(MEDIA_TYPES)}"}
        length = int(handler.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_MEDIA_BYTES:
            return 413, {"error": f"videos are at most {MAX_MEDIA_BYTES // (1024 * 1024)} MB"}
        token = secrets.token_hex(8)
        stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(name).stem).strip("_")[:60] or "video"
        target = self.folder / token / f"{stem}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        remaining = length
        with target.open("wb") as file:
            while remaining > 0:
                chunk = handler.rfile.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    break
                file.write(chunk)
                remaining -= len(chunk)
        if remaining:
            shutil.rmtree(target.parent, ignore_errors=True)
            return 400, {"error": "the upload stopped before the end of the video"}
        return 200, {"path": f"{MEDIA_PREFIX}/{token}/{target.name}"}

    def serve(self, handler, path: str) -> None:
        """Sends a video, or the part a player's Range header asks for, which players need to
        start and seek without downloading the whole file."""
        parts = path.removeprefix(MEDIA_PREFIX + "/").split("/")
        file = self.folder / parts[0] / parts[-1]
        if len(parts) != 2 or not TOKEN.fullmatch(parts[0]) or Path(parts[1]).name != parts[1] or not file.is_file():
            handler._json(404, {"error": "no such video"})
            return
        size = file.stat().st_size
        start, end, status = 0, size - 1, 200
        asked = RANGE.match(handler.headers.get("Range", "").strip())
        if asked and (asked.group(1) or asked.group(2)):
            if asked.group(1):
                start = int(asked.group(1))
                end = min(int(asked.group(2)), size - 1) if asked.group(2) else size - 1
            else:
                # "bytes=-500" is the last 500 bytes
                start = max(size - int(asked.group(2)), 0)
            if start > end:
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{size}")
                handler.send_header("Content-Length", "0")
                handler.end_headers()
                return
            status = 206
        handler.send_response(status)
        handler.send_header("Content-Type", MEDIA_TYPES.get(file.suffix.lower(), "application/octet-stream"))
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Cache-Control", "max-age=3600")
        handler.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        handler.end_headers()
        remaining = end - start + 1
        with file.open("rb") as video:
            video.seek(start)
            while remaining > 0:
                chunk = video.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    return
                try:
                    handler.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    # Players drop a request when they seek elsewhere
                    return
                remaining -= len(chunk)
