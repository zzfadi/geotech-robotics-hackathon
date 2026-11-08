"""python3 -m unittest discover -s control_center/tests -t .   (from the repo root)"""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from control_center import telemetry
from control_center.demo import CHUNK, SPEED_LIMIT, THRESHOLD_MS, run_demo
from control_center.server import Catalog, read_limited, serve

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA_FILE = os.path.join(os.path.dirname(HERE), 'telemetry.schema.json')


def event(kind, **fields):
    base = {'schema': telemetry.SCHEMA, 'kind': kind, 'ts': 1.0, 'seq': 0, 'session': 's1',
            'source': {'component': 'test', 'host': 'h'}}
    base.update(fields)
    return base


class SchemaTest(unittest.TestCase):
    def test_a_good_event_of_each_kind_validates(self):
        good = {
            'session': dict(phase='start', robot='arm', purpose='sim'),
            'task': dict(phase='step', name='Hold my beer', step='Lift', step_index=2),
            'network': dict(phase='received', request_id='r1', rtt_ms=712.0),
            'mode': dict(mode='sync', median_ms=912, threshold_ms=850),
            'chunk': dict(request_id='r1', actions=48, arrived_at_action=30, blended=16, skipped=0),
            'command': dict(requested_m_s=2.11, applied_m_s=0.85, limit_m_s=0.85, clamped=True),
            'decision': dict(by='safety', what='Clamped speed', why='Over the limit', routine=False),
            'camera': dict(role='head', ok=False, checks={'colors': 'fail', 'aspect': 'ok'}),
            'frame': dict(role='head', path='frames/r1.png', width=320, height=176),
            'contact': dict(what='drop'),
            'state': dict(q=[0, 0, 0, 0, 0, 0], grip=1.0),
        }
        self.assertEqual(set(good), set(telemetry.KINDS))
        for kind, fields in good.items():
            self.assertEqual(telemetry.validate(event(kind, **fields)), [], kind)

    def test_problems_are_named(self):
        self.assertIn('network.request_id missing', telemetry.validate(event('network', phase='sent')))
        self.assertTrue(telemetry.validate(event('mode', mode='fast', median_ms=1, threshold_ms=850)))
        self.assertTrue(telemetry.validate(event('decision', by='someone', what='x', why='y')))
        self.assertTrue(telemetry.validate(event('camera', role='head', ok=True, checks={'colours': 'ok'})))
        self.assertTrue(telemetry.validate(event('command', requested_m_s=1, applied_m_s=1, limit_m_s=1, clamped=1)))
        self.assertTrue(telemetry.validate(event('nope')))
        self.assertTrue(telemetry.validate({'kind': 'contact', 'what': 'drop'}))

    def test_the_loop_never_skips_ahead(self):
        problems = telemetry.validate(event('chunk', request_id='r', actions=48, arrived_at_action=48, blended=0, skipped=3))
        self.assertTrue(any('never skips' in p for p in problems))

    def test_frame_paths_stay_inside_the_session(self):
        for path in ('/etc/passwd', '../other/frames/x.png', 'frames/../../x.png'):
            self.assertTrue(telemetry.validate(event('frame', role='head', path=path, width=1, height=1)), path)

    def test_schema_file_is_in_sync(self):
        with open(SCHEMA_FILE) as fh:
            self.assertEqual(json.load(fh), telemetry.json_schema(),
                             'Regenerate with: python3 -m control_center schema > control_center/telemetry.schema.json')


class WriterTest(unittest.TestCase):
    def test_writes_reads_and_refuses_bad_events(self):
        with tempfile.TemporaryDirectory() as root:
            with telemetry.Writer(root, session='t-1', component='test', clock=lambda: 5.0) as w:
                w.emit('session', phase='start', robot='arm', purpose='sim', station=None)
                w.emit('network', phase='sent', request_id='r1')
                with self.assertRaises(ValueError):
                    w.emit('network', phase='sideways', request_id='r2')
            path = os.path.join(root, 't-1', 'events.jsonl')
            events, nxt = telemetry.read(path)
            self.assertEqual([e['seq'] for e in events], [0, 1])
            self.assertNotIn('station', events[0])          # None fields are dropped
            self.assertEqual(nxt, os.path.getsize(path))
            with open(path, 'a') as fh:
                fh.write('{"half": ')                         # a writer mid-line
            more, nxt2 = telemetry.read(path, nxt)
            self.assertEqual((more, nxt2), ([], nxt))

    def test_rejects_unsafe_session_ids(self):
        with tempfile.TemporaryDirectory() as root:
            for bad in ('../x', 'a/b', '.hidden', 'x' * 90):
                with self.assertRaises(ValueError):
                    telemetry.Writer(root, session=bad)


class DemoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        cls.sid = run_demo(cls.root, seconds=300, live=False, seed=3, start=1_000_000.0, session='demo-test')
        cls.events, _ = telemetry.read(os.path.join(cls.root, cls.sid, 'events.jsonl'))

    def test_every_event_is_valid_and_in_order(self):
        self.assertEqual([p for p in map(telemetry.validate, self.events) if p], [])
        self.assertEqual([e['seq'] for e in self.events], list(range(len(self.events))))
        self.assertEqual([e['ts'] for e in self.events], sorted(e['ts'] for e in self.events))

    def test_follows_the_workshop_numbers(self):
        kinds = lambda k: [e for e in self.events if e['kind'] == k]
        self.assertEqual(self.events[0]['purpose'], 'demo')
        self.assertEqual(self.events[-1]['kind'], 'session')
        self.assertTrue(all(c['applied_m_s'] <= SPEED_LIMIT for c in kinds('command')))
        self.assertTrue(any(c['clamped'] for c in kinds('command')))
        self.assertTrue(all(c['actions'] == CHUNK and c['skipped'] == 0 for c in kinds('chunk')))
        modes = kinds('mode')
        self.assertTrue(modes, 'expected at least one switch in 5 minutes')
        for m in modes:
            self.assertEqual(m['mode'] == 'sync', m['median_ms'] > THRESHOLD_MS)
        self.assertTrue(all((f['width'], f['height']) == (320, 176) for f in kinds('frame')))
        for f in kinds('frame'):
            self.assertTrue(os.path.isfile(os.path.join(self.root, self.sid, f['path'])))
        self.assertTrue(any(not c['ok'] for c in kinds('camera')), 'the colour fault should show')

    def test_catalog_summary(self):
        s = Catalog(self.root).sessions()[0]
        self.assertEqual((s['id'], s['ended'], s['live'], s['purpose']), (self.sid, True, False, 'demo'))
        self.assertGreater(s['attempts'], 0)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp()
        with telemetry.Writer(cls.root, session='srv-1', component='test') as w:
            w.emit('session', phase='start', robot='arm', purpose='sim')
            abs_path, rel = w.frame_path('r1.png')
            with open(abs_path, 'wb') as fh:
                fh.write(b'\x89PNG\r\n\x1a\nfake')
            w.emit('frame', role='head', path=rel, width=320, height=176)
        with open(os.path.join(cls.root, 'secret.txt'), 'w') as fh:
            fh.write('nope')
        cls.httpd = serve(cls.root, '127.0.0.1', 0)
        cls.base = 'http://127.0.0.1:%d' % cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def get(self, path):
        try:
            with urllib.request.urlopen(self.base + path) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_ui_and_api(self):
        status, headers, body = self.get('/')
        self.assertEqual(status, 200)
        self.assertIn(b'Control center', body)
        self.assertIn("default-src 'self'", headers['Content-Security-Policy'])
        for asset in ('/app.js', '/style.css'):
            self.assertEqual(self.get(asset)[0], 200)
        sessions = json.loads(self.get('/api/sessions')[2])['sessions']
        self.assertEqual([s['id'] for s in sessions], ['srv-1'])
        page = json.loads(self.get('/api/sessions/srv-1/events?from=0')[2])
        self.assertEqual(len(page['events']), 2)
        self.assertEqual(page['next'], page['size'])
        tail = json.loads(self.get('/api/sessions/srv-1/events?from=%d' % page['next'])[2])
        self.assertEqual(tail['events'], [])
        status, headers, body = self.get('/api/sessions/srv-1/frames/r1.png')
        self.assertEqual((status, headers['Content-Type'], body[:4]), (200, 'image/png', b'\x89PNG'))

    def test_refuses_paths_outside_the_sessions(self):
        for path in ('/api/sessions/..%2F/events', '/api/sessions/../secret.txt', '/api/sessions/srv-1/frames/..%2F..%2Fsecret.txt',
                     '/api/sessions/srv-1/frames/events.jsonl', '/api/sessions/nope/events', '/server.py', '/ui/app.js',
                     '/api/sessions/srv-1/events?from=abc'):
            self.assertIn(self.get(path)[0], (400, 404), path)

    def test_is_read_only(self):
        req = urllib.request.Request(self.base + '/api/sessions', data=b'{}', method='POST')
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(req)
        self.assertEqual(cm.exception.code, 501)

    def test_read_limited_stops_at_a_whole_line(self):
        path = os.path.join(self.root, 'srv-1', 'events.jsonl')
        events, nxt = read_limited(path, 0)
        self.assertEqual((len(events), nxt), (2, os.path.getsize(path)))


if __name__ == '__main__':
    unittest.main()
