import json
import socket
import threading
import time
import unittest
from urllib.request import Request, urlopen

import cv2
import numpy as np

from browser_camera import serve


class BrowserCameraTest(unittest.TestCase):
    def test_idle_preconnection_does_not_block_clock_or_frame(self):
        class Publisher:
            seq = sent = 0

            def begin(self):
                pass

            def publish(self, *_):
                self.seq += 1
                self.sent += 1
                return True

        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        publisher = Publisher()
        worker = threading.Thread(target=serve, args=(publisher, 1, port), daemon=True)
        worker.start()
        for attempt in range(30):
            try:
                idle = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except ConnectionRefusedError:
                if attempt == 29:
                    raise
                time.sleep(0.02)
        base = f"http://127.0.0.1:{port}"
        with idle:
            with urlopen(base + "/clock", timeout=2) as reply:
                timestamp = json.load(reply)["station_ns"]
            _, jpeg = cv2.imencode(".jpg", np.zeros((2, 2, 3), dtype=np.uint8))
            frame = Request(
                base + "/frame",
                data=jpeg.tobytes(),
                headers={
                    "Origin": base,
                    "X-Camera-Session": "fixture",
                    "X-Station-Ns": timestamp,
                    "X-Capture-Timing": "browser_capture",
                },
            )
            with urlopen(frame, timeout=2) as reply:
                self.assertEqual(json.load(reply)["published"], 1)
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
