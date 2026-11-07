"""Mac loopback camera ingress for users capturing with a browser's camera permission."""

import json
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import cv2
import numpy as np


def serve(publisher, max_frames, port=17864):
    page = Path(__file__).with_name("webcam.html").read_bytes()
    active = None
    last_frame = 0

    class Handler(BaseHTTPRequestHandler):
        def send_body(self, code, content, mime):
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def reply(self, code, **fields):
            self.send_body(code, json.dumps(fields).encode(), "application/json")

        def do_GET(self):
            if self.path == "/":
                self.send_body(200, page, "text/html; charset=utf-8")
            elif self.path == "/clock":
                self.reply(200, station_ns=str(time.monotonic_ns()))
            elif self.path == "/status":
                self.reply(
                    200,
                    received=publisher.seq,
                    published=publisher.sent,
                    age_ms=round((time.monotonic_ns() - last_frame) / 1e6)
                    if last_frame
                    else None,
                )
            else:
                self.send_error(404)

        def do_POST(self):
            nonlocal active, last_frame
            if self.headers.get("Origin") not in (
                f"http://localhost:{port}",
                f"http://127.0.0.1:{port}",
            ):
                return self.reply(403, error="Open the local station page")
            sender = self.headers.get("X-Camera-Session", "")
            if not 1 <= len(sender) <= 64:
                return self.reply(400, error="Missing camera session")
            now = time.monotonic_ns()
            if self.path == "/end":
                if active == sender:
                    active = None
                return self.reply(200, stopped=True)
            if self.path != "/frame":
                return self.send_error(404)
            if active and active != sender and now - last_frame < 2_000_000_000:
                return self.reply(409, error="Another webcam session is running")
            try:
                n = int(self.headers["Content-Length"])
                stamp = int(self.headers["X-Station-Ns"])
                timing = self.headers["X-Capture-Timing"]
                if (
                    not 0 < n <= 1_500_000
                    or not -50_000_000 <= now - stamp <= 500_000_000
                    or timing not in ("browser_capture", "browser_presentation")
                ):
                    raise ValueError("Frame time or size out of range")
                payload = self.rfile.read(n)
                frame = cv2.imdecode(np.frombuffer(payload, np.uint8), cv2.IMREAD_COLOR)
                if frame is None or frame.shape[1] > 1920 or frame.shape[0] > 1080:
                    raise ValueError("Invalid camera image")
                if active != sender:
                    publisher.begin()
                    active = sender
                last_frame = now
                detected = publisher.publish(frame, stamp, timing)
            except (TypeError, ValueError, KeyError, cv2.error) as exc:
                return self.reply(400, error=str(exc))
            self.reply(
                200, detected=detected, received=publisher.seq, published=publisher.sent
            )

        def log_message(self, *_):
            pass

    with HTTPServer(("127.0.0.1", port), Handler) as server:
        print(
            f"Open http://localhost:{port}/ in the Mac browser. Images are processed in memory.",
            flush=True,
        )
        while max_frames is None or publisher.seq < max_frames:
            server.handle_request()
