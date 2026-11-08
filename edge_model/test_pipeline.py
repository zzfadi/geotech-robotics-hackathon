"""Pipeline tests: collect -> dataset -> train -> evaluate on a tiny bench run (well under 60 s).

Run from the repo root:
  arm/.venv/bin/python -m unittest discover -s edge_model -p 'test_*.py' -t .
"""
import json
import os
import shutil
import tempfile
import unittest

import numpy as np

from edge_model import collect as col
from edge_model import dataset as ds
from edge_model import evaluate as ev
from edge_model import scenarios
from edge_model import train as tr
from edge_model.bench import OBS_NAMES
from edge_model.model import Policy


class PipelineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix='edge-model-test-')
        seeds = [1000, 1001, 1002, 1003]
        cls.episodes, cls.splits = col.collect(seeds, tables=(0, 1), variants=('clean',))
        cls.data = os.path.join(cls.tmp, 'dataset')
        cls.manifest = ds.write(cls.data, cls.episodes, cls.splits, {'test': True})
        cls.ckpt = os.path.join(cls.tmp, 'policy.npz')
        cls.policy, cls.meta = tr.train(cls.data, cls.ckpt, {'epochs': 3})

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_split_is_by_seed_and_disjoint(self):
        s = col.split_seeds(list(range(10)))
        self.assertEqual((len(s['train']), len(s['val']), len(s['heldout'])), (7, 1, 2))
        self.assertFalse(set(s['train']) & set(s['heldout']))
        self.assertFalse(set(s['val']) & set(s['heldout']))
        for e in self.episodes:
            self.assertIn(e['seed'], self.splits[e['split']])

    def test_observations_match_model_inputs(self):
        e = self.episodes[0]
        self.assertEqual(e['obs'].shape[1], len(OBS_NAMES))
        self.assertEqual(e['obs'].shape[1], 14)
        self.assertEqual(e['act'].shape[1], 7)
        self.assertTrue(set(np.unique(e['act'][:, 6])) <= {0.0, 1.0})

    def test_demonstrations_pass_the_gate_and_failures_are_separate(self):
        for split in ('train', 'val', 'heldout'):
            _, _, z = ds.load(self.data, split)
            self.assertTrue(z['ep_passed'].all() if len(z['ep_passed']) else True)
        _, _, z = ds.load(self.data, 'eval_failures')
        self.assertFalse(z['ep_passed'].any())
        self.assertEqual(self.manifest['source'], 'bench-expert')
        self.assertFalse(self.manifest['dream_export_eligible'])

    def test_manifest_hash_is_checked(self):
        path = os.path.join(self.data, 'val.npz')
        self.assertEqual(ds.sha256(path), self.manifest['files']['val']['sha256'])
        bad = os.path.join(self.tmp, 'tampered')
        shutil.copytree(self.data, bad)
        with open(os.path.join(bad, 'val.npz'), 'ab') as fh:
            fh.write(b'x')
        with self.assertRaises(ValueError):
            ds.load(bad, 'val')

    def test_checkpoint_provenance(self):
        loaded = Policy.load(self.ckpt)
        for key in ('config', 'data_manifest_sha256', 'numpy', 'python', 'train_seconds', 'data_source'):
            self.assertIn(key, loaded.meta)
        self.assertEqual(loaded.meta['data_source'], 'bench-expert')
        with open(os.path.splitext(self.ckpt)[0] + '.json') as fh:
            side = json.load(fh)
        self.assertEqual(side['checkpoint_sha256'], ds.sha256(self.ckpt))

    def test_evaluate_writes_valid_sessions(self):
        from control_center import telemetry
        out = os.path.join(self.tmp, 'eval')
        sessions = os.path.join(self.tmp, 'sessions')
        summary = ev.evaluate(self.ckpt, [1003], [0], out, sessions, log=None)
        self.assertEqual(set(summary['rows']), {'%s/%s' % r for r in ev.ROWS})
        self.assertEqual(summary['telemetry']['invalid_events'], 0)
        self.assertEqual(summary['telemetry']['sessions'], len(ev.ROWS))
        kinds_by_row = {}
        for sid in os.listdir(sessions):
            events, _ = telemetry.read(os.path.join(sessions, sid, 'events.jsonl'))
            self.assertTrue(events)
            self.assertTrue(all(not telemetry.validate(e) for e in events))
            self.assertEqual(events[0]['kind'], 'session')
            self.assertEqual(events[0]['source']['component'], 'edge-model')
            self.assertEqual(events[-1]['kind'], 'session')
            kinds_by_row[sid] = {e['kind'] for e in events}
            for e in events:
                if e['kind'] == 'chunk':
                    self.assertEqual(e['skipped'], 0)
        lag = [k for sid, k in kinds_by_row.items() if '-lag-' in sid]
        self.assertTrue(lag and all({'network', 'chunk', 'decision'} <= k for k in lag))
        self.assertEqual(summary['rows']['lag/hold']['lag']['skipped_actions'], 0)


class ScenarioTest(unittest.TestCase):
    def test_round_trips_look_like_dream(self):
        r = ev.realized_rtts(range(1000, 1020), range(9))
        self.assertTrue(660 < r['median_ms'] < 770, r)
        self.assertGreater(r['over_850_ms_fraction'], 0.05)
        self.assertGreater(r['failed_fraction'], 0.0)
        self.assertLess(r['failed_fraction'], 0.1)

    def test_round_trips_are_common_random_numbers(self):
        a, b = scenarios.RoundTrips(1000, 3), scenarios.RoundTrips(1000, 3)
        self.assertEqual(a.values[:50], b.values[:50])

    def test_speed_clamp(self):
        from edge_model.kinematics import Arm, HOME
        arm = Arm()
        q, req, app, clamped = scenarios.clamp_speed(arm, HOME, HOME + 0.6)
        self.assertTrue(clamped)
        self.assertGreater(req, scenarios.SPEED_LIMIT)
        self.assertLessEqual(app, scenarios.SPEED_LIMIT + 1e-6)
        q, req, app, clamped = scenarios.clamp_speed(arm, HOME, HOME + 0.001)
        self.assertFalse(clamped)

    def test_hold_on_late_never_skips_and_stays_under_the_limit(self):
        r = scenarios.lag(1001, 2, 'hold')
        self.assertEqual(r['lag']['skipped_actions'], 0)   # measured: played + still queued == accepted
        self.assertGreater(r['lag']['teacher_actions_played'], 0)
        self.assertAlmostEqual(r['lag']['frozen_before_first_reply_s'] + r['lag']['frozen_after_first_reply_s'],
                               r['lag']['frozen_s'], places=6)
        self.assertGreater(r['lag']['frozen_before_first_reply_s'], 0)   # hold waits for the first chunk
        self.assertLessEqual(r['speed']['peak_applied'], scenarios.SPEED_LIMIT + 1e-6)
        self.assertGreater(r['lag']['chunks_played'], 0)

    def test_expert_reference_passes(self):
        r = scenarios.autonomous(1001, 0, 'expert')
        self.assertTrue(r['success'], r['reasons'])
        self.assertFalse(r['wrong_cup'])


if __name__ == '__main__':
    unittest.main()
