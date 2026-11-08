"""python3 -m control_center <command>

  serve      open the control center on http://127.0.0.1:8780
  demo       write a live demo session (simulated robot) while you watch
  history    write a few finished demo sessions for the history view
  validate   check a session's events against the schema
  schema     print the JSON Schema of the telemetry contract
"""
import argparse
import json
import os
import sys
import threading
import time

from control_center import telemetry
from control_center.server import DEFAULT_PORT

DEFAULT_DATA = os.environ.get('CONTROL_CENTER_DATA') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'local', 'sessions')


def main(argv=None):
    p = argparse.ArgumentParser(prog='python3 -m control_center', description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--data', default=DEFAULT_DATA, help='sessions folder (default %(default)s, or $CONTROL_CENTER_DATA)')
    sub = p.add_subparsers(dest='command')
    s = sub.add_parser('serve', help='serve the UI')
    s.add_argument('--host', default='127.0.0.1', help='bind address; keep 127.0.0.1 and use an SSH tunnel')
    s.add_argument('--port', type=int, default=DEFAULT_PORT,
                   help='default %(default)s; 8765 is the arm server\'s port')
    s.add_argument('--demo', action='store_true', help='also run a live demo session and seed some history')
    d = sub.add_parser('demo', help='write a live simulated session')
    d.add_argument('--minutes', type=float, default=15)
    d.add_argument('--speed', type=float, default=1.0, help='time compression; 1 is real time')
    d.add_argument('--seed', type=int)
    d.add_argument('--no-faults', action='store_true', help='skip the camera colour fault')
    h = sub.add_parser('history', help='write finished demo sessions')
    h.add_argument('--count', type=int, default=3)
    v = sub.add_parser('validate', help='validate a session')
    v.add_argument('session', help='session id or path to an events.jsonl')
    sub.add_parser('schema', help='print the JSON Schema')
    a = p.parse_args(argv)

    if a.command == 'schema':
        print(json.dumps(telemetry.json_schema(), indent=2))
    elif a.command == 'validate':
        path = a.session if os.path.isfile(a.session) else os.path.join(a.data, a.session, 'events.jsonl')
        events, _ = telemetry.read(path)
        bad = [(e.get('seq'), telemetry.validate(e)) for e in events]
        bad = [(seq, probs) for seq, probs in bad if probs]
        for seq, probs in bad[:20]:
            print('seq %s: %s' % (seq, '; '.join(probs)))
        print('%d events, %d invalid' % (len(events), len(bad)))
        sys.exit(1 if bad else 0)
    elif a.command == 'history':
        seed_history(a.data, a.count)
    elif a.command == 'demo':
        from control_center.demo import run_demo
        sid = telemetry.new_session_id('demo')
        print('Writing live demo session %s to %s (Ctrl-C to stop)' % (sid, a.data))
        try:
            run_demo(a.data, seconds=a.minutes * 60, live=True, speed=a.speed, seed=a.seed,
                     faults=not a.no_faults, session=sid)
        except KeyboardInterrupt:
            pass
    elif a.command == 'serve':
        from control_center.server import serve
        if a.demo:
            from control_center.demo import run_demo
            has_sessions = os.path.isdir(a.data) and any(os.scandir(a.data))
            if not has_sessions:
                seed_history(a.data, 3)
            threading.Thread(target=run_demo, args=(a.data,), daemon=True,
                             kwargs={'seconds': 3600, 'live': True, 'session': telemetry.new_session_id('demo')}).start()
        httpd = serve(a.data, a.host, a.port)
        print('Control center on http://%s:%d  (sessions in %s)' % (a.host, a.port, a.data))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
    else:
        p.print_help()


def seed_history(root, count):
    from control_center.demo import run_demo
    now = time.time()
    for i in range(count):
        start = now - (count - i) * 2400 - 600
        sid = 'demo-' + time.strftime('%Y%m%d-%H%M%S', time.localtime(start)) + '-hist%d' % i
        run_demo(root, seconds=360, live=False, seed=100 + i, start=start, session=sid)
        print('Wrote finished demo session', sid)
