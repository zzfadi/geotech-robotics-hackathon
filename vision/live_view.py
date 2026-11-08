"""Live Jetson camera view: pose skeletons plus bottle/cup boxes, streamed as MJPEG on 127.0.0.1:8790.

Watch-only: it never talks to an arm. Runs on the Orin Nano with the FP16 TensorRT engines and owns
the camera, so stop vision.run first. View it from a Mac through ssh -N -L 8790:127.0.0.1:8790 orin-arm.

    cd ~/hub-tests/geotech-robotics-hackathon && YOLO_CONFIG_DIR=~/hub-tests/yolo-config \
      ~/hub-tests/venv-trt10/bin/python -m vision.live_view
"""
import cv2, threading, time, os
os.environ.setdefault("YOLO_OFFLINE", "1")
from ultralytics import YOLO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

det = YOLO(os.path.expanduser("~/hub-tests/engines-fp16/yolo26n.engine"), task="detect")
pose = YOLO(os.path.expanduser("~/hub-tests/engines-fp16/yolo26n-pose.engine"), task="pose")
cap = cv2.VideoCapture(0, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
out = {"jpg": None}


def loop():
    while True:
        ok, f = cap.read()
        if not ok:
            time.sleep(0.05)
            continue
        t = time.time()
        img = pose(f, device=0, verbose=False)[0].plot()
        d = det(f, device=0, verbose=False)[0]
        for b, c, k in zip(d.boxes.xyxy.tolist(), d.boxes.conf.tolist(), d.boxes.cls.tolist()):
            name = d.names[int(k)]
            if name not in ("bottle", "cup", "wine glass"):
                continue
            x0, y0, x1, y1 = map(int, b)
            cv2.rectangle(img, (x0, y0), (x1, y1), (0, 200, 255), 3)
            cv2.putText(img, f"{name} {c:.2f}", (x0, max(20, y0 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
        ms = (time.time() - t) * 1000
        cv2.putText(img, f"Jetson Orin Nano - TensorRT FP16 - {ms:.0f} ms", (10, 465), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        out["jpg"] = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()


threading.Thread(target=loop, daemon=True).start()


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path != "/stream":
            b = b'<html><body style="margin:0;background:#000;display:flex;justify-content:center"><img src="/stream" style="height:100vh"></body></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b)
            return
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
        self.end_headers()
        try:
            while True:
                if out["jpg"]:
                    self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + out["jpg"] + b"\r\n")
                time.sleep(1 / 15)
        except Exception:
            pass


ThreadingHTTPServer(("127.0.0.1", 8790), H).serve_forever()
