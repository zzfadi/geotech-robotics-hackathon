"""Read-only HTTP server for the control center. Stdlib only.

GET /                          the UI
GET /api/sessions              every session, newest first, with a summary
GET /api/sessions/<id>/events?from=<byte offset>
                               events from that offset and the next offset;
                               the UI polls this to follow a live session
GET /api/sessions/<id>/frames/<file>
                               an image the model saw

It never writes, never commands the arm and never holds a key. Bind it to
127.0.0.1 (the default) and reach it over an SSH tunnel from another machine.
"""
import json
import mimetypes
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from control_center.telemetry import SESSION_ID, read

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ui')
FRAME = re.compile(r'^[A-Za-z0-9._-]{1,120}\.(png|jpe?g)$')
MAX_CHUNK = 4 * 1024 * 1024
# 8765 belongs to the arm tools (pickup_server, handoff). Never share it.
DEFAULT_PORT = 8780
LIVE_IF_WRITTEN_WITHIN = 20.0


class Catalog:
    """Session summaries, cached by file size so a live file stays cheap."""

    def __init__(self, root):
        self.root = os.path.abspath(root)
        self._cache = {}
        self._lock = threading.Lock()

    def path(self, session):
        if not SESSION_ID.match(session or ''):
            return None
        p = os.path.join(self.root, session, 'events.jsonl')
        return p if os.path.isfile(p) else None

    def sessions(self):
        out = []
        if not os.path.isdir(self.root):
            return out
        for name in os.listdir(self.root):
            p = self.path(name)
            if p:
                out.append(self.summary(name, p))
        out.sort(key=lambda s: s.get('started') or 0, reverse=True)
        return out

    def summary(self, name, path):
        import time
        st = os.stat(path)
        with self._lock:
            cached = self._cache.get(name)
            if cached and cached[0] == st.st_size:
                summary = dict(cached[1])
            else:
                summary = self._summarize(name, path)
                self._cache[name] = (st.st_size, summary)
                summary = dict(summary)
        summary['live'] = not summary['ended'] and time.time() - st.st_mtime < LIVE_IF_WRITTEN_WITHIN
        return summary

    @staticmethod
    def _summarize(name, path):
        events, _ = read(path)
        s = {'id': name, 'events': len(events), 'started': None, 'last': None, 'ended': False,
             'robot': None, 'purpose': None, 'station': None, 'attempts': 0, 'succeeded': 0}
        for e in events:
            ts = e.get('ts')
            if s['started'] is None:
                s['started'] = ts
            s['last'] = ts
            if e.get('kind') == 'session':
                if e.get('phase') == 'start':
                    s.update(robot=e.get('robot'), purpose=e.get('purpose'), station=e.get('station'))
                else:
                    s['ended'] = True
            elif e.get('kind') == 'task' and e.get('phase') == 'end':
                s['attempts'] += 1
                s['succeeded'] += e.get('outcome') == 'success'
        return s


def make_handler(catalog):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'ControlCenter/0.1'

        def log_message(self, fmt, *args):
            pass

        def _send(self, code, body, ctype='application/json', cache='no-store'):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', cache)
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; img-src 'self' data:; style-src 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            parts = [p for p in url.path.split('/') if p]
            if not parts or parts == ['index.html']:
                return self._file(os.path.join(UI, 'index.html'))
            if len(parts) == 1 and parts[0] in ('app.js', 'style.css'):
                return self._file(os.path.join(UI, parts[0]))
            if parts == ['api', 'sessions']:
                return self._send(200, {'sessions': catalog.sessions()})
            if len(parts) == 4 and parts[:2] == ['api', 'sessions'] and parts[3] == 'events':
                path = catalog.path(parts[2])
                if not path:
                    return self._send(404, {'error': 'no such session'})
                try:
                    start = max(0, int(parse_qs(url.query).get('from', ['0'])[0]))
                except ValueError:
                    return self._send(400, {'error': 'from must be a byte offset'})
                size = os.path.getsize(path)
                start = min(start, size)
                events, nxt = read_limited(path, start)
                return self._send(200, {'events': events, 'next': nxt, 'size': size})
            if len(parts) == 5 and parts[:2] == ['api', 'sessions'] and parts[3] == 'frames':
                if not catalog.path(parts[2]) or not FRAME.match(parts[4]):
                    return self._send(404, {'error': 'no such frame'})
                return self._file(os.path.join(catalog.root, parts[2], 'frames', parts[4]), cache='max-age=3600')
            return self._send(404, {'error': 'not found'})

        def _file(self, path, cache='no-cache'):
            if not os.path.isfile(path):
                return self._send(404, {'error': 'not found'})
            ctype = mimetypes.guess_type(path)[0] or 'application/octet-stream'
            if ctype.startswith('text/') or ctype.endswith('javascript'):
                ctype += '; charset=utf-8'
            with open(path, 'rb') as fh:
                return self._send(200, fh.read(), ctype, cache)

    return Handler


def read_limited(path, start):
    """Like telemetry.read, but at most MAX_CHUNK bytes per call."""
    with open(path, 'rb') as fh:
        fh.seek(start)
        data = fh.read(MAX_CHUNK)
    end = data.rfind(b'\n') + 1
    events = []
    for line in data[:end].splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            continue
    return events, start + end


def serve(root, host='127.0.0.1', port=DEFAULT_PORT):
    os.makedirs(root, exist_ok=True)
    httpd = ThreadingHTTPServer((host, port), make_handler(Catalog(root)))
    httpd.daemon_threads = True
    return httpd
