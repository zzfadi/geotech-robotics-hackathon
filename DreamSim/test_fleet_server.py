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
           'position_ik': True, 'pad_contacts': True, 'action_rows': 12, 'cycles': 10, 'secret': 'dropped'} for i in range(9)]


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

    def test_all_means_nine_and_unavailable_tables_are_visible(self):
        self.request('/api/fleet/worker/hello', {'tables': TABLES[:3]}, self.token)
        fleet = self.request('/api/fleet')
        self.assertEqual(len(fleet['tables']), 9)
        self.assertFalse(fleet['tables'][-1]['ready'])
        self.assertEqual(self.status('/api/fleet/command', {'tables': 'all'}), 503)
        self.assertIsNone(self.server.store.fleet['command'])
        command = self.request('/api/fleet/command', {'tables': [1]})
        self.assertEqual(command['tables'], [1])

    def test_preview_preference_survives_server_restart_default_on(self):
        self.assertTrue(self.server.store.fleet['preview_enabled'])
        self.request('/api/fleet/preview',{'enabled':False})
        restored=Store(Path(self.dir.name))
        self.assertFalse(restored.fleet['preview_enabled'])
        self.request('/api/fleet/preview',{'enabled':True})
        self.assertTrue(Store(Path(self.dir.name)).fleet['preview_enabled'])

    def test_single_run_accepts_passive_images_without_extra_results(self):
        self.request('/api/fleet/worker/hello',{'tables':TABLES},self.token)
        command=self.request('/api/fleet/command',{'tables':[0]})
        self.request('/api/fleet/worker/next',token=self.token)
        self.request('/api/fleet/worker/status',{'request_id':command['id'],'phase':'ready',
                     'tables':{'T00':{'calls':1}},'frames':{'T00':JPEG,'T08':JPEG}},self.token)
        status=self.request('/api/fleet')
        self.assertEqual(status['command']['tables'],[0])
        self.assertEqual(set(status['status']['tables']),{'T00'})
        self.assertEqual(set(self.request('/api/fleet/frames')['frames']),{'T00','T08'})
        self.assertEqual(self.status('/api/fleet/worker/status',{'request_id':command['id'],
                         'frames':{'T09':JPEG}},self.token),400)

    def test_legacy_worker_cannot_accept_reset(self):
        self.request('/api/fleet/worker/hello', {'tables': TABLES}, self.token)
        self.assertEqual(self.status('/api/fleet/reset',{}),503)
        self.assertIsNone(self.server.store.fleet['reset'])

    def test_reset_waits_for_run_cleanup_and_claims_once_without_command(self):
        self.request('/api/fleet/worker/hello', {'tables': TABLES, 'reset_supported': True}, self.token)
        command=self.request('/api/fleet/command',{'tables':[0]})
        self.request('/api/fleet/worker/next',token=self.token)
        reset=self.request('/api/fleet/reset',{'seed':42})
        self.assertTrue(self.server.store.fleet['command']['stop'])
        self.assertEqual(self.request('/api/fleet/reset',{})['id'],reset['id'])
        self.assertNotIn('reset',self.request('/api/fleet/worker/next',token=self.token))
        self.assertEqual(self.status('/api/fleet/command',{'tables':[0]}),409)
        self.request('/api/fleet/worker/done',{'request_id':command['id'],'status':'stopped','results':[]},self.token)
        claimed=self.request('/api/fleet/worker/next',token=self.token)
        self.assertEqual(claimed['reset']['id'],reset['id']);self.assertIsNone(claimed['command'])
        self.assertNotIn('reset',self.request('/api/fleet/worker/next',token=self.token))
        self.request('/api/fleet/worker/reset-progress',{'request_id':reset['id']},self.token)
        self.assertEqual(self.request('/api/fleet')['reset']['status'],'running')
        self.server.store.fleet['frames']={'T00':JPEG}
        self.server.store.fleet['frame_times']={'T00':100}
        self.request('/api/fleet/worker/reset-done',{'request_id':reset['id'],'error':None},self.token)
        self.assertEqual(self.server.store.fleet['frames'],{'T00':JPEG})
        self.assertEqual(self.server.store.fleet['frame_times'],{'T00':100})
        state=self.request('/api/fleet')
        self.assertEqual(state['reset']['status'],'done');self.assertTrue(state['preview_enabled'])
        self.assertEqual(state['recording']['request_id'],command['id'])

    def test_review_plan_is_reproducible_without_starting(self):
        payload = {'tables': 'all', 'mode': 'random', 'seed': 42, 'randomize': True}
        plan = self.request('/api/fleet/plan', payload)
        self.assertEqual(len(plan['tables']), 9)
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
        self.assertEqual(len(recording['results']), 9)
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

    def test_original_table_zero_and_all_nine_share_fleet_queue(self):
        self.request('/api/fleet/worker/hello', {'tables': TABLES}, self.token)
        self.assertEqual(self.request('/api/route', {'prompt':'pick up red'})['tables'], [0])
        command=self.request('/api/command', {'prompt':'Table 1: pick up red'})
        self.assertEqual(command['tables'], [0])
        self.assertIsNone(self.server.store.command)
        self.server.store.fleet['command']=None
        command=self.request('/api/command', {'prompt':'All tables: pick up red'})
        self.assertEqual(command['tables'], list(range(9)))
        self.assertEqual([t['display_number'] for t in command['plan']['tables']], list(range(1,10)))
        for n in (10,11):
            self.assertEqual(self.status('/api/command', {'prompt':'Table %d: lift'%n}),400)
        self.assertEqual(self.status('/api/fleet/worker/hello', {'tables':TABLES,'expected_tables':list(range(1,9))}, self.token),400)

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

    def test_unclaimed_queued_batch_does_not_pin_a_dead_worker(self):
        self.request('/api/fleet/worker/hello', {'tables': TABLES}, self.token)
        command = self.request('/api/fleet/command', {'tables': [1]})
        self.server.store.fleet['worker_at'] = 0  # first worker died before claiming
        self.worker_id = 'b'*32
        self.request('/api/fleet/worker/hello', {'tables': TABLES}, self.token)
        self.assertEqual(self.request('/api/fleet/worker/next', token=self.token)['command']['id'], command['id'])

    def test_full_fleet_cycle_and_mutual_exclusion(self):
        self.request('/api/fleet/worker/hello', {'tables': TABLES}, self.token)
        public = self.request('/api/fleet')
        self.assertTrue(public['online'])
        self.assertNotIn('secret', public['tables'][0])
        self.assertEqual(self.status('/api/fleet/command', {'tables': [9]}), 400)
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
