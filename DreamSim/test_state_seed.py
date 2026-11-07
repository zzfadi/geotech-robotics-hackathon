import json,tempfile,unittest
from pathlib import Path
from state_seed import recover_state

class StateSeedTest(unittest.TestCase):
    def test_rejected_playback_recovers_observed_pose(self):
        with tempfile.TemporaryDirectory() as directory:
            paths=[Path(directory)/name for name in ('playback','observation','initial')]
            actual={'q_left':[.1]*6,'q_right':[.2]*6,'gap_left_m':.03,'gap_right_m':.04}
            paths[0].write_text(json.dumps({'done':True,'error':'Rejected speed'}))
            paths[1].write_text(json.dumps({'state':actual}))
            paths[2].write_text(json.dumps(dict(actual,q_left=[0]*6)))
            self.assertEqual(recover_state(*paths),actual)
            paths[0].write_text(json.dumps(dict(actual,q_left=[.3]*6)))
            self.assertEqual(recover_state(*paths)['q_left'],[.3]*6)
    def test_missing_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory)/'missing'
            with self.assertRaises(ValueError):recover_state(p,p,p)
