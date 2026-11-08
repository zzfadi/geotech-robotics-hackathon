import json
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fleet_worker  # noqa: E402
from fleet_worker import hold_action, lift_verdict, TableRun  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'Unreal/TheHumanTouch/Tools'))
from maker_arm_actions import retime  # noqa: E402
from maker_arm_fleet_layout import normalize_variant  # noqa: E402

STATE = {'q_left': [0, -2.1318205591, 0.4402033511, 1.691617186, 0, 0], 'q_right': [0]*6,
         'gap_left_m': .08, 'gap_right_m': .04}


class Helpers(unittest.TestCase):
    def test_idle_preview_preserves_capture_time_and_identical_fresh_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            image = root/'T01.jpg'
            image.write_bytes(b'\xff\xd8pixels\xff\xd9')
            calls = []
            server = types.SimpleNamespace(api=lambda path, data: calls.append((path, data)))
            preview = fleet_worker.IdlePreview(None, root, server)
            packet = {'frames': {'T01': str(image)}, 'frame_times': {'T01': 123.5}}
            fleet_worker.write_json(root/'preview.json', packet)
            preview.publish()
            preview.publish()
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1]['frame_times'], {'T01': 123.5})
            packet['frame_times']['T01'] = 125.5
            fleet_worker.write_json(root/'preview.json', packet)
            preview.publish()
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[1][1]['frame_times']['T01'], 125.5)

    def test_worker_lock_excludes_another_checkout_process(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp:
            fleet_worker.acquire_worker_lock(Path(tmp))
            code = 'import sys; sys.path.insert(0, sys.argv[1]); import fleet_worker; fleet_worker.acquire_worker_lock(fleet_worker.Path(sys.argv[2]))'
            try:
                other = subprocess.run([sys.executable, '-c', code, str(Path(fleet_worker.__file__).parent), tmp], capture_output=True, text=True)
                self.assertNotEqual(other.returncode, 0)
                self.assertIn('Another fleet worker owns', other.stderr)
            finally:
                fleet_worker._WORKER_LOCK.close()
                fleet_worker._WORKER_LOCK = None

    def test_concurrency_evidence_counts_distinct_overlapping_tables(self):
        results = [{'table': 'T%02d' % i, 'calls': 1,
                    'requests': [{'started_unix': i * .01, 'finished_unix': 2}],
                    'motion_chunks': [{'rows': 12}]} for i in range(1, 11)]
        evidence = fleet_worker.concurrency_evidence(results)
        self.assertEqual(evidence['peak_concurrent_request_tables'], 10)
        self.assertEqual(len(evidence['tables_with_motion_rows']), 10)
        for i, r in enumerate(results):
            r['requests'] = [{'started_unix': i, 'finished_unix': i + 1}]
        self.assertEqual(fleet_worker.concurrency_evidence(results)['peak_concurrent_request_tables'], 1)

    def test_hold_action_passes_runtime_validation(self):
        action = hold_action(STATE, 12)
        chunks, timing = retime(action, STATE, 20)
        self.assertEqual(len(chunks['left']), 13)
        self.assertAlmostEqual(chunks['left'][-1][6]*.104825, .08)

    def test_lift_verdict_requires_free_simulated_hold(self):
        props = {'MakerStation_Approximate_Block_Red': {'initial_z_cm': 78.5, 'max_z_cm': 89.4,
                 'height_above_start_cm': 10.8, 'current_slow_above_10cm_seconds': 2.4,
                 'max_slow_above_10cm_seconds': 2.4, 'physics_simulated': True, 'gravity_enabled': True, 'attached_to': None}}
        verdict = lift_verdict({'props': props}, 'MakerStation_Approximate_Block_Red')
        self.assertTrue(verdict['lift_held'])
        props['MakerStation_Approximate_Block_Red']['attached_to'] = 'MakerArm_left_link_007'
        self.assertFalse(lift_verdict({'props': props}, 'MakerStation_Approximate_Block_Red')['lift_held'])
        props['MakerStation_Approximate_Block_Red'].update(attached_to=None, current_slow_above_10cm_seconds=1.0)
        self.assertFalse(lift_verdict({'props': props}, 'MakerStation_Approximate_Block_Red')['lift_held'])
        self.assertFalse(lift_verdict(None, 'x')['measured'])


class FakeRuntime(unittest.TestCase):
    """Drive TableRun against a simulated runtime that honours the file protocol."""

    def test_dry_run_cycles_through_file_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            variant = normalize_variant({'prompt': 'p', 'cycles': 3, 'initial_state': STATE}, 1)
            table = run/'T01'
            table.mkdir()
            args = types.SimpleNamespace(dry_run=True)
            runner = TableRun(variant, run, args)
            seq = {'n': 0}

            def runtime_tick():
                fleet = {'phase': 'ready', 'tables': {'T01': {'error': None, 'props': {
                    'MakerStation_Approximate_Block_Red': {'initial_z_cm': 78.5, 'max_z_cm': 78.6,
                                                           'height_above_start_cm': .1, 'physics_simulated': True}}}}}
                fleet_worker.write_json(run/'fleet.json', fleet)
                command = fleet_worker.read_json(table/'command.json')
                if command and command['seq'] > seq['n']:
                    seq['n'] = command['seq']
                    fleet_worker.write_json(table/'playback.json', {'seq': seq['n'], 'done': True, 'error': None})
                fleet_worker.write_json(table/'observation.json', {'captured_unix': time.time(), 'moving': False,
                                                                   'captured_monotonic': time.monotonic(),
                                                                   'frames': {}, 'state': STATE})
            import threading
            stop = threading.Event()

            def loop():
                while not stop.is_set():
                    runtime_tick()
                    time.sleep(.05)
            thread = threading.Thread(target=loop, daemon=True)
            thread.start()
            try:
                result = runner()
            finally:
                stop.set()
                thread.join()
            self.assertIsNone(result['error'], result['events'])
            self.assertEqual(result['cycles_run'], 3)
            self.assertEqual(result['stopped_reason'], 'cycles exhausted')
            report = json.loads((table/'action-03.json').read_text())
            self.assertEqual(report['controller_source'], 'dry-run hold')

    def test_expired_session_refreshes_state_and_records_both_requests(self):
        from unittest.mock import patch
        class Expired(Exception):
            pass
        class Client:
            def __init__(self):
                self.observations = []
            def set_observation(self, frames, obs, timestamp):
                self.observations.append(obs)
            def get_action(self):
                if len(self.observations) == 1:
                    raise Expired('renewed')
                return {'action': 'fresh'}
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run/'T01').mkdir()
            v = normalize_variant({'prompt': 'p', 'initial_state': STATE}, 1)
            client = Client()
            runner = TableRun(v, run, types.SimpleNamespace(dry_run=False), client)
            first = {'state': STATE, 'frames': {}, 'captured_unix': time.time(), 'captured_monotonic': 1}
            second = {**first, 'state': {**STATE, 'q_left': [1]*6}, 'captured_monotonic': 2}
            runner.fresh = lambda after: second
            errors = types.ModuleType('dream.errors')
            errors.DreamSessionExpired = Expired
            with patch.dict(sys.modules, {'dream.errors': errors}):
                action, used = runner.infer(first)
            self.assertEqual(client.observations[1]['state.left_arm'], [1]*6)
            self.assertIs(used, second)
            evidence = json.loads((run/'T01/requests.json').read_text())
            self.assertEqual(len(evidence), 2)
            self.assertIn('error', evidence[0])
            self.assertTrue(evidence[1]['returned_action'])
            self.assertGreaterEqual(evidence[1]['finished_unix'], evidence[1]['started_unix'])

    def test_capacity_errors_retry_without_losing_table(self):
        from unittest.mock import patch
        class Client:
            calls = 0
            def set_observation(self, *args, **kwargs):
                pass
            def get_action(self):
                self.calls += 1
                if self.calls < 2:
                    raise RuntimeError('DA-2000 NO_CAPACITY (pool_full)')
                return {'action': 'real'}
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run/'T01').mkdir()
            v = normalize_variant({'prompt': 'p', 'initial_state': STATE}, 1)
            runner = TableRun(v, run, types.SimpleNamespace(dry_run=False), Client())
            packet = {'state': STATE, 'frames': {}, 'captured_unix': time.time(), 'captured_monotonic': 1}
            runner.fresh = lambda after: packet
            errors = types.ModuleType('dream.errors')
            errors.DreamSessionExpired = type('Expired', (Exception,), {})
            with patch.dict(sys.modules, {'dream.errors': errors}), patch.object(fleet_worker.time, 'monotonic', side_effect=[0, 10]):
                action, used = runner.infer(packet)
            self.assertEqual(runner.result['calls'], 2)
            self.assertEqual(action, {'action': 'real'})
            self.assertIn('NO_CAPACITY', runner.result['requests'][0]['error'])

    def test_rejected_chunk_surfaces_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run/'T01').mkdir()
            variant = normalize_variant({'prompt': 'p', 'cycles': 2, 'initial_state': STATE}, 1)
            fleet_worker.write_json(run/'fleet.json', {'phase': 'ready', 'tables': {'T01': {'error': None}}})
            fleet_worker.write_json(run/'T01/observation.json', {'captured_unix': time.time()+5, 'moving': False,
                                                                 'captured_monotonic': 0, 'frames': {}, 'state': STATE})
            fleet_worker.write_json(run/'T01/playback.json', {'seq': 1, 'done': True, 'error': 'stale pose'})
            result = TableRun(variant, run, types.SimpleNamespace(dry_run=True))()
            self.assertIn('stale pose', result['error'])


if __name__ == '__main__':
    unittest.main()
