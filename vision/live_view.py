"""Presenter dashboard on the Jetson: live camera, the real perception pipeline and the full picture.

Runs the same pieces as ``vision.run`` (YOLO detector + pose, MomentTracker, TurnGate, ExecutorRouter) on
the live camera and serves one page on 127.0.0.1:8790: annotated video, the current moment / gate /
router decision, latency, an event timeline and the end-to-end pipeline with what is live here and what
is shown separately. Watch-only: it never talks to an arm. It owns the camera, so stop vision.run first.

    cd ~/hub-tests/geotech-robotics-hackathon && YOLO_CONFIG_DIR=~/hub-tests/yolo-config \
      ~/hub-tests/venv-trt10/bin/python -m vision.live_view
    # on the Mac: ssh -N -L 8790:127.0.0.1:8790 orin-arm, then open http://127.0.0.1:8790
    # Mac camera instead: ~/hub-mac/.venv/bin/python -m vision.live_view --mac  (port 8792; weights in ~/hub-mac/weights)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .detections import FrameDetections
from .hybrid import ExecutorRouter, HandoffCoordinator
from .yolo_source import detections_from_result

ENGINES = Path("~/hub-tests/engines-fp16").expanduser()
COLORS = {"hand": (60, 60, 255), "bottle": (80, 220, 80), "cup": (80, 220, 80), "can": (80, 220, 80),
          "person": (255, 170, 60)}


class Pipeline:
    def __init__(self, args):
        import cv2
        from ultralytics import YOLO

        self.cv2 = cv2
        self.detect = YOLO(str(args.detect), task="detect")
        self.pose = YOLO(str(args.pose), task="pose")
        self.kwargs = {"imgsz": 640, "conf": 0.25, "verbose": False, "device": args.device}
        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
        self.capture = cv2.VideoCapture(args.camera, backend)
        self.capture.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.router = ExecutorRouter(target="real")
        self.coordinator = HandoffCoordinator(router=self.router)
        self.zone = self.coordinator.tracker.config.handoff
        self.jpg = None
        self.lock = threading.Lock()
        self.latencies = deque(maxlen=300)
        self.stamps = deque(maxlen=60)
        self.events = deque(maxlen=14)
        self.frames = 0
        self.started = time.time()
        self.state = {}
        self.last_gate = None
        self.host = args.host

    def _event(self, kind, detail):
        self.events.appendleft({"t": time.strftime("%H:%M:%S"), "kind": kind, "detail": detail})

    def step(self):
        cv2 = self.cv2
        tick = self.coordinator.tick()
        captured = time.monotonic()
        ok, frame = self.capture.read()
        if not ok:
            time.sleep(0.1)  # camera missing or not authorized: don't spin
            frames, pose_result = FrameDetections(captured, (), valid=False, error="read_failed"), None
        else:
            try:
                det = self.detect(frame, **self.kwargs)[0]
                pose_result = self.pose(frame, **self.kwargs)[0]
                found = list(detections_from_result(det)) + list(detections_from_result(pose_result))
                frames = FrameDetections(captured, tuple(found), source="ultralytics")
            except Exception as exc:
                frames, pose_result = FrameDetections(captured, (), valid=False, error=str(exc)), None
        record = self.coordinator.on_frame(frames)
        done = time.monotonic()
        latency = 1000 * (done - captured)
        self.latencies.append(latency)
        self.stamps.append(done)
        self.frames += 1

        moment, gate = record["moment"], record["gate"]
        for e in moment["events"]:
            self._event(e["kind"], e.get("reason", ""))
        if gate["state"] != self.last_gate:
            self._event("gate " + gate["state"], gate.get("reason", ""))
            self.last_gate = gate["state"]
        for a in tick["actions"] + record["actions"]:
            self._event("action " + a["action"], a.get("executor", ""))

        counts = {}
        for d in frames.detections:
            counts[d.label] = counts.get(d.label, 0) + 1
        plan = self.router.choose()
        ordered = sorted(self.latencies)
        pick = lambda q: round(ordered[min(len(ordered) - 1, int(q * len(ordered)))], 1)
        fps = (len(self.stamps) - 1) / (self.stamps[-1] - self.stamps[0]) if len(self.stamps) > 1 else 0.0
        state = {
            "moment": moment["state"], "moment_reason": moment.get("reason"),
            "motion_ok": moment.get("motion_ok"), "valid": frames.valid,
            "gate": gate["state"], "gate_reason": gate.get("reason"), "phase": record["phase"],
            "router": {"executor": plan.executor, "mode": plan.mode, "reason": plan.reason,
                       "late_ms": self.router.late_ms},
            "counts": counts, "latency_ms": round(latency, 1), "p50": pick(0.5), "p95": pick(0.95),
            "fps": round(fps, 1), "frames": self.frames, "uptime_s": int(time.time() - self.started),
            "events": list(self.events), "host": self.host,
        }

        if ok:
            img = pose_result.plot(boxes=False, labels=False) if pose_result is not None else frame
            h, w = img.shape[:2]
            z = self.zone
            cv2.rectangle(img, (int(z.x_min * w), int(z.y_min * h)), (int(z.x_max * w), int(z.y_max * h)),
                          (0, 200, 255), 2)
            cv2.putText(img, "handoff zone", (int(z.x_min * w) + 4, int(z.y_min * h) + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
            for d in frames.detections:
                x0, y0, x1, y1 = d.box
                c = COLORS.get(d.label, (180, 180, 180))
                cv2.rectangle(img, (int(x0 * w), int(y0 * h)), (int(x1 * w), int(y1 * h)), c, 2)
                cv2.putText(img, "{} {:.2f}".format(d.label, d.confidence), (int(x0 * w), max(16, int(y0 * h) - 6)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.55, c, 2)
            jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])[1].tobytes()
        else:
            jpg = None
        with self.lock:
            self.state = state
            if jpg:
                self.jpg = jpg

    def run(self):
        while True:
            self.step()


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hold My Beer · Jetson</title><style>
:root{--bg:#0b0d12;--panel:#141821;--line:#232a36;--ink:#e8ecf2;--mute:#8a94a6;--live:#2ecc71;--sep:#4aa3ff;--next:#f5a623;--hold:#ff5d5d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.4 -apple-system,system-ui,Segoe UI,Roboto,sans-serif}
header{display:flex;align-items:baseline;gap:14px;padding:14px 20px;border-bottom:1px solid var(--line)}
h1{font-size:22px;margin:0}header .sub{color:var(--mute)}header .dot{margin-left:auto;color:var(--live);font-weight:600}
.grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(300px,1fr);gap:14px;padding:14px 20px;align-items:start}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:14px}
.video img{width:100%;border-radius:8px;display:block;background:#000;aspect-ratio:4/3;object-fit:contain}
.label{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
.big{font-size:28px;font-weight:700}.reason{color:var(--mute);font-size:13px}
.cards{display:grid;grid-template-columns:1fr 1fr;gap:10px}.card{background:#0f131a;border:1px solid var(--line);border-radius:10px;padding:10px 12px}
.hold{color:var(--hold)}.go{color:var(--live)}
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px}.metrics div{background:#0f131a;border:1px solid var(--line);border-radius:10px;padding:8px;text-align:center}
.metrics b{display:block;font-size:20px}.metrics span{color:var(--mute);font-size:12px}
.events{list-style:none;margin:10px 0 0;padding:0;max-height:260px;overflow:auto;font-family:ui-monospace,Menlo,monospace;font-size:13px}
.events li{padding:4px 0;border-bottom:1px solid var(--line)}.events time{color:var(--mute);margin-right:8px}
.flow{padding:0 20px 18px}.steps{display:grid;grid-template-columns:repeat(6,1fr);gap:10px}
.step{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px;position:relative}
.step h3{margin:0 0 4px;font-size:15px}.step p{margin:0;color:var(--mute);font-size:13px}
.chip{display:inline-block;font-size:11px;font-weight:700;padding:2px 8px;border-radius:99px;margin-bottom:8px}
.c-live{background:rgba(46,204,113,.15);color:var(--live)}.c-sep{background:rgba(74,163,255,.15);color:var(--sep)}.c-next{background:rgba(245,166,35,.15);color:var(--next)}
.note{color:var(--mute);font-size:13px;margin-top:10px}
@media (max-width:900px){.grid{grid-template-columns:1fr}.steps{grid-template-columns:1fr 1fr}}
</style></head><body>
<header><h1>The Human Touch · Hold My Beer</h1><span class="sub" id="host">live perception</span><span class="dot" id="up">● LIVE</span></header>
<div class="grid">
 <div class="panel video"><div class="label" id="camlabel">Live webcam · YOLO26n + pose</div><img id="cam" alt="live camera"></div>
 <div class="panel">
  <div class="cards">
   <div class="card"><div class="label">What it sees</div><div class="big" id="moment">–</div><div class="reason" id="mreason"></div></div>
   <div class="card"><div class="label">Safety gate</div><div class="big" id="gate">–</div><div class="reason" id="greason"></div></div>
   <div class="card"><div class="label">Robot may move?</div><div class="big" id="motion">–</div><div class="reason">only when no hand is in the arm's path</div></div>
   <div class="card"><div class="label">Who plans the move</div><div class="big" id="router">–</div><div class="reason" id="rreason"></div></div>
  </div>
  <div class="metrics"><div><b id="lat">–</b><span>ms / frame</span></div><div><b id="p95">–</b><span>p95 ms</span></div><div><b id="fps">–</b><span>fps</span></div><div><b id="seen">–</b><span>in view</span></div></div>
  <div class="label" style="margin-top:12px">Timeline</div><ul class="events" id="events"></ul>
 </div>
</div>
<div class="flow"><div class="label">The full loop we are building</div><div class="steps">
 <div class="step"><span class="chip c-live">LIVE HERE</span><h3>1 · Camera</h3><p>Webcam on the Jetson watches the person, the hand and the bottle.</p></div>
 <div class="step"><span class="chip c-live">LIVE HERE</span><h3>2 · Edge perception</h3><p>YOLO26n + pose on the Orin Nano GPU, about 30 ms per frame, no cloud.</p></div>
 <div class="step"><span class="chip c-live">LIVE HERE</span><h3>3 · Moments + safety gate</h3><p>Hand entered, offer, release. The arm holds while a hand is in its path.</p></div>
 <div class="step"><span class="chip c-live">LIVE HERE</span><h3>4 · Hybrid router</h3><p>Dream plans the full move when the network answers within 850 ms; a local controller takes over otherwise.</p></div>
 <div class="step"><span class="chip c-sep">SHOWN BY SALEH</span><h3>5 · Dream Labs</h3><p>Natural-language prompt: pick up the bottle, hand it over, let go, return home.</p></div>
 <div class="step"><span class="chip c-sep">SHOWN BY DENYS</span><h3>6 · Arms + Unreal twin</h3><p>Real Maker Arms and the Unreal Table 10 twin rehearse the handover in parallel.</p></div>
</div><div class="note"><span class="chip c-next">NEXT</span> Wire steps 3–4 into 5–6 so the safety gate and router decide the real handover end to end. Today each piece runs on its own; this page is watch-only and never moves an arm.</div></div>
<script>
const $=id=>document.getElementById(id);
const nice=s=>(s||'').replace(/_/g,' ');
async function poll(){try{const s=await (await fetch('/state',{cache:'no-store'})).json();
 $('moment').textContent=s.moment||'–';$('mreason').textContent=nice(s.moment_reason);
 $('gate').textContent=s.gate;$('gate').className='big '+(s.gate==='HOLD'?'hold':'go');$('greason').textContent=nice(s.gate_reason);
 $('motion').textContent=s.motion_ok?'YES':'NO';$('motion').className='big '+(s.motion_ok?'go':'hold');
 $('router').textContent=s.router.executor==='dream'?'Dream':'Local';$('rreason').textContent=nice(s.router.reason)+' · '+nice(s.router.mode)+' · '+s.router.late_ms+' ms line';
 $('lat').textContent=s.latency_ms;$('p95').textContent=s.p95;$('fps').textContent=s.fps;
 const c=s.counts||{};$('seen').textContent=['person','hand','bottle','cup'].filter(k=>c[k]).map(k=>c[k]+' '+k).join(', ')||'nothing';
 $('events').innerHTML=(s.events||[]).map(e=>`<li><time>${e.t}</time>${nice(e.kind)} <span class="reason">${nice(e.detail)}</span></li>`).join('');
 if(s.host){$('host').textContent=s.host.title;$('camlabel').textContent=s.host.camera}
 $('up').textContent='● LIVE · '+s.frames+' frames';$('up').style.color='var(--live)';
}catch(e){$('up').textContent='● reconnecting';$('up').style.color='var(--hold)';}}
setInterval(poll,250);poll();
const cam=$('cam');let busy=false;
function next(){if(busy)return;busy=true;const u='/frame.jpg?t='+Date.now();const im=new Image();
 im.onload=()=>{cam.src=u;busy=false};im.onerror=()=>{busy=false};
 setTimeout(()=>{busy=false},1500);im.src=u}
setInterval(next,70);next();
</script></body></html>"""


def serve(pipe: Pipeline, port: int):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body: bytes, kind: str):
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/state":
                with pipe.lock:
                    body = json.dumps(pipe.state).encode()
                return self._send(body, "application/json")
            if self.path.startswith("/frame.jpg"):
                with pipe.lock:
                    jpg = pipe.jpg
                if not jpg:
                    self.send_response(503)
                    self.end_headers()
                    return
                return self._send(jpg, "image/jpeg")
            if self.path != "/stream":
                return self._send(PAGE.encode(), "text/html; charset=utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=f")
            self.end_headers()
            try:
                while True:
                    with pipe.lock:
                        jpg = pipe.jpg
                    if jpg:
                        self.wfile.write(b"--f\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n")
                    time.sleep(1 / 15)
            except Exception:
                pass

    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()


def main(argv=None) -> int:
    os.environ.setdefault("YOLO_OFFLINE", "1")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--camera", type=int, default=0)
    p.add_argument("--detect", type=Path, default=ENGINES / "yolo26n.engine")
    p.add_argument("--pose", type=Path, default=ENGINES / "yolo26n-pose.engine")
    p.add_argument("--port", type=int, default=8790)
    p.add_argument("--device", default="0", help="0 for the Jetson GPU, mps on a Mac, cpu otherwise")
    p.add_argument("--mac", action="store_true", help="Mac built-in camera with .pt weights on the Apple GPU")
    p.add_argument("--weights", type=Path, default=Path("~/hub-mac/weights").expanduser())
    a = p.parse_args(argv)
    if a.device.isdigit():
        a.device = int(a.device)
    if a.mac:
        a.detect, a.pose, a.device = a.weights / "yolo26n.pt", a.weights / "yolo26n-pose.pt", "mps"
        if a.port == 8790:  # 8790 is the Mac end of the Jetson tunnel
            a.port = 8792
        a.host = {"title": "MacBook · live perception (stand-in for the Jetson)",
                  "camera": "MacBook camera · YOLO26n + pose · Apple GPU"}
    else:
        a.host = {"title": "Jetson Orin Nano · live perception",
                  "camera": "Live webcam on the Jetson · YOLO26n + pose · TensorRT FP16"}
    pipe = Pipeline(a)
    threading.Thread(target=pipe.run, daemon=True).start()
    serve(pipe, a.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
