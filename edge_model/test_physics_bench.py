"""Optional MuJoCo test; skipped when mujoco is not installed (the rest stays numpy-only).

  uv run --python 3.12 --with numpy --with mujoco==4.0.0 \
    python -m unittest edge_model.test_physics_bench
"""
import importlib.util
import unittest

HAS_MUJOCO = importlib.util.find_spec('mujoco') is not None


@unittest.skipUnless(HAS_MUJOCO, 'mujoco not installed')
class PhysicsBenchTest(unittest.TestCase):
    def test_model_matches_repo_fk(self):
        from edge_model import physics_bench
        self.assertLess(physics_bench.check_fk(samples=5), 1e-5)

    def test_expert_episode_runs_and_reports_physics(self):
        from edge_model import physics_bench
        r = physics_bench.run(0, 1080, steps=40)
        self.assertTrue(r['physics_simulated'])
        self.assertFalse(r['dream_export_eligible'])
        self.assertIn('close', r['phases_seen'])


if __name__ == '__main__':
    unittest.main()
