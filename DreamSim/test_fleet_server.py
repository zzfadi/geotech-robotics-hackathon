import base64
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

from debug_server import Handler, Store

JPEG = base64.b64encode(b'\xff\xd8thumb\xff\xd9').decode()
TABLES = [{'index': i, 'name': 'variant-%d' % i, 'prompt': 'p', 'checkpoint': 'dreamlabs/dream-1-beta-flash',
           'position_ik': True, 'pad_contacts': True, 'action_rows': 12, 'cycles': 10, 'secret': 'dropped'} for i in range(1, 11)]


class FleetServer(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.store = Store(Path(self.dir.name))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = 'http://127.0.0.1:%d' % self.server.server_port
        self.token = self.server.store.token
        self.worker_id = 'a'*32

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.dir.cleanup()

    def request(self, path, data=None, token=None, origin=None):
        headers = {'Content-Type': 'application/json'}
        if token and path.startswith('/api/fleet/worker/'):
            if data is not None: data = dict(data,worker_id=self.worker_id)
            elif path=='/api/fleet/worker/next': path += '?worker_id='+self.worker_id
        if token:
            headers['Authorization'] = 'Bearer ' + token
        if origin:
            headers['Origin'] = origin
        req = urllib.request.Request(self.url + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
        with urllib.request.urlopen(req) as r:
            return json.load(r)

    def status(self, path, data=None, token=None, origin=None):
        with self.assertRaises(urllib.error.HTTPError) as e:
            self.request(path, data, token, origin)
        return e.exception.code

    def test_worker_routes_need_token(self):
        self.assertEqual(self.status('/api/fleet/worker/next'), 401)
        self.assertEqual(self.status('/api/fleet/worker/hello', {'tables': TABLES}), 401)
        self.assertEqual(self.status('/api/fleet/command', {'tables': 'all'}, origin='https://evil.example'), 403)

    def test_offline_worker_refuses_commands(self):
        self.request('/api/fleet/worker/hello', {'tables': TABLES}, self.token)
        self.server.store.fleet['worker_at'] = 0
        self.assertEqual(self.status('/api/fleet/command', {'tables': [1]}), 503)

    def test_all_means_ten_and_unavailable_tables_are_visible(self):
        self.request('/api/fleet/worker/hello', {'tables': TABLES[:3]}, self.token)
        fleet = self.request('/api/fleet')
        self.assertEqual(len(fleet['tables']), 10)
        self.assertFalse(fleet['tables'][-1]['ready'])
        self.assertEqual(self.status('/api/fleet/command', {'tables': 'all'}), 503)
        self.assertIsNone(self.server.store.fleet['command'])
        command = self.request('/api/fleet/command', {'tables': [1]})
        self.assertEqual(command['tables'], [1])

    def test_review_plan_is_reproducible_without_starting(self):
        payload = {'tables': 'all', 'mode': 'random', 'seed': 42, 'randomize': True}
        plan = self.request('/api/fleet/plan', payload)
        self.assertEqual(len(plan['tables']), 10)
        self.assertIsNone(self.server.store.fleet['command'])
        self.request('/api/fleet/worker/hello', {'tables': TABLES}, self.token)
        command = self.request('/api/fleet/command', payload)
        self.assertEqual(plan, command['plan'])

    def test_missing_results_are_errors_and_missing_videos_not_advertised(self):
        self.request('/api/fleet/worker/hello', {'tables': TABLES}, self.token)
        command = self.request('/api/fleet/command', {'tables': 'all'})
        self.request('/api/fleet/worker/next', token=self.token)
        self.request('/api/fleet/worker/done', {'request_id': command['id'], 'status': 'done', 'results': []}, self.token)
        recording = self.request('/api/fleet')['recording']
        self.assertEqual(recording['recorded_tables'], [])
        self.assertEqual(len(recording['results']), 10)
        self.assertTrue(all(r['outcome'] == 'error' for r in recording['results']))

    def test_main_prompt_routes_explicit_targets_without_wrong_queue(self):
        self.request('/api/fleet/worker/hello', {'tables': TABLES}, self.token)
        route=self.request('/api/route',{'prompt':'Table 3: pick up the red cube'})
        self.assertEqual(route['tables'],[2])
        command=self.request('/api/command',{'prompt':'Table 3: pick up the red cube'})
        self.assertEqual(command['tables'],[2])
        self.assertEqual(command['prompt'],'pick up the red cube')
        self.assertIsNone(self.server.store.command)
        self.assertEqual(self.status('/api/command',{'prompt':'Table 12: move'}),400)

    def test_idle_preview_requires_lease_and_actual_capture_time(self):
        import time
        self.request('/api/fleet/worker/hello', {'tables': TABLES}, self.token)
        self.request('/api/fleet/preview', {'enabled': True})
        self.assertTrue(self.request('/api/fleet/worker/next',token=self.token)['preview_enabled'])
        captured=time.time()-2
        self.request('/api/fleet/worker/preview', {'frames':{'T02':JPEG},'frame_times':{'T02':captured}},self.token)
        frames=self.request('/api/fleet/frames')
        self.assertEqual(frames['received_at']['T02'],captured)
        self.assertEqual(frames['frames']['T02'],JPEG)
        self.assertIsNone(self.server.store.fleet['command'])
        self.worker_id='b'*32
        self.assertEqual(self.status('/api/fleet/worker/preview',{'frames':{'T02':JPEG},'frame_times':{'T02':captured}},self.token),409)

    def test_worker_lease_and_claim_prevent_duplicate_execution(self):
        self.request('/api/fleet/worker/hello', {'tables': TABLES}, self.token)
        command = self.request('/api/fleet/command', {'tables': [2]})
        first = self.request('/api/fleet/worker/next', token=self.token)['command']
        self.assertEqual(first['id'], command['id'])
        self.assertIsNone(self.request('/api/fleet/worker/next', token=self.token)['command'])
        self.worker_id = 'b'*32
        self.assertEqual(self.status('/api/fleet/worker/hello', {'tables': TABLES}, self.token),409)
        self.assertEqual(self.status('/api/fleet/worker/next', token=self.token),409)
        self.assertEqual(self.status('/api/fleet/worker/done', {'request_id':command['id'],'results':[]}, self.token),409)
        self.assertEqual(self.server.store.fleet['command']['status'],'running')

    def test_full_fleet_cycle_and_mutual_exclusion(self):
        self.request('/api/fleet/worker/hello', {'tables': TABLES}, self.token)
        public = self.request('/api/fleet')
        self.assertTrue(public['online'])
        self.assertNotIn('secret', public['tables'][0])
        self.assertEqual(self.status('/api/fleet/command', {'tables': [0]}), 400)
        self.assertEqual(self.status('/api/fleet/command', {'tables': [1, 1]}), 400)
        self.assertEqual(self.status('/api/fleet/command', {'tables': []}), 400)
        command = self.request('/api/fleet/command', {'tables': [3, 1], 'prompt': 'Pick up the red cube.'})
        self.assertEqual(command['tables'], [1, 3])
        self.assertEqual(self.status('/api/fleet/command', {'tables': 'all'}), 409)
        # The main table must wait for the fleet (they share one editor).
        self.server.store.worker_at = 1e12
        self.assertEqual(self.status('/api/command', {'prompt': 'move'}), 409)
        claimed = self.request('/api/fleet/worker/next', token=self.token)['command']
        self.assertEqual((claimed['id'], claimed['status']), (command['id'], 'running'))
        self.assertEqual(self.status('/api/fleet/worker/status', {'request_id': 'f'*32, 'tables': {}}, self.token), 400)
        reply = self.request('/api/fleet/worker/status', {'request_id': command['id'], 'phase': 'ready',
                                                           'tables': {'T01': {'cycles_run': 1}}, 'frames': {'T01': JPEG}}, self.token)
        self.assertFalse(reply['stop'])
        self.assertEqual(self.request('/api/fleet/frames')['frames']['T01'], JPEG)
        self.assertEqual(self.request('/api/fleet')['status']['tables']['T01']['cycles_run'], 1)
        self.request('/api/fleet/stop', {})
        self.assertTrue(self.request('/api/fleet/worker/status', {'request_id': command['id'], 'tables': {}}, self.token)['stop'])
        video = base64.b64encode(b'0000ftyp' + b'x'*100).decode()
        self.assertEqual(self.status('/api/fleet/worker/video', {'request_id': command['id'], 'table': '../x', 'video': video}, self.token), 400)
        self.request('/api/fleet/worker/video', {'request_id': command['id'], 'table': 'T01', 'video': video}, self.token)
        self.request('/api/fleet/worker/done', {'request_id': command['id'], 'status': 'stopped', 'results': [{'table': 'T01'}]}, self.token)
        fleet = self.request('/api/fleet')
        self.assertEqual(fleet['command']['status'], 'stopped')
        self.assertEqual(fleet['recording']['tables'], [1, 3])
        with urllib.request.urlopen(self.url + '/api/fleet/recording/%s/T01.mp4' % command['id']) as r:
            self.assertEqual(r.read()[4:8], b'ftyp')
        # A queued main command blocks the fleet in turn.
        self.request('/api/command', {'prompt': 'move'})
        self.assertEqual(self.status('/api/fleet/command', {'tables': 'all'}), 409)
        self.assertTrue((Path(self.dir.name)/'fleet-recording.json').exists())


if __name__ == '__main__':
    unittest.main()
