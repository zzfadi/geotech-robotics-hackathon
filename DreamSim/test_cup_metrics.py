import copy
import unittest
from cup_metrics import cup_metrics,candidate
from fleet_planning import build_plan,apply_plan

class CupMetricTests(unittest.TestCase):
    def test_exact_custom_prompt_no_blue_target_success_objective(self):
        task=build_plan([0],prompt='Grab a cup')['tables'][0]
        self.assertEqual(task['prompt'],'Grab a cup');self.assertIsNone(task['objective'])
        self.assertIsNone(task['motion_side'])
        config={'tables':[{'index':0,'prompt':'old blue task','objective':{'kind':'lift_hold'},'target':{'label':'MakerStation_Prop_Cup_03'}}]}
        out=apply_plan(config,build_plan([0],prompt='Grab a cup'))['tables'][0]
        self.assertEqual(out['prompt'],'Grab a cup');self.assertIsNone(out['objective'])
    def status(self):
        p={'height_above_start_cm':11.,'current_slow_above_10cm_seconds':3.,'speed_cm_s':1.,'physics_simulated':True,'gravity_enabled':True,'attached_to':None}
        return {'sampled_unix':100.,'props':{'MakerStation_Prop_Cup':copy.deepcopy(p),'MakerStation_Prop_Cup_03':copy.deepcopy(p)}}
    def test_no_cross_object_hold_or_false_grasp(self):
        m=cup_metrics(self.status(),now=100.1)
        self.assertFalse(any(x['lift_held'] for x in m.values())) # 3+3 is not6
        self.assertIn(candidate(m),m)
        for x in m.values():self.assertFalse(x['grasp_verified']);self.assertFalse(x['task_completed'])
    def test_all_cups_measured_but_stale_attached_nonfinite_rejected(self):
        s=self.status();s['props']['MakerStation_Prop_Cup_03']['current_slow_above_10cm_seconds']=5.1
        self.assertTrue(cup_metrics(s,100.1)['MakerStation_Prop_Cup_03']['lift_held'])
        self.assertIsNone(candidate(cup_metrics(s,103)))
        for key,value in [('attached_to','jaw'),('speed_cm_s',float('nan')),('height_above_start_cm',0)]:
            q=copy.deepcopy(s)
            for p in q['props'].values():p[key]=value
            self.assertIsNone(candidate(cup_metrics(q,100.1)))

if __name__=='__main__':unittest.main()
