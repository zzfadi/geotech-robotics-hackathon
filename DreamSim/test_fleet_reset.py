import copy
import unittest
from fleet_reset import reset_config, apply_idle_reset, HOME_Q
from fleet_planning import apply_plan, build_plan

class Reset(unittest.TestCase):
    def test_only_nine_tables_seeded_no_stale_target_scale(self):
        config={'tables':[{'index':i,'prompt':'cup','target':{'label':'MakerStation_Prop_Cup_03','scale':[2,2,2]}} for i in range(9)]}
        original=copy.deepcopy(config)
        reset=reset_config(config,42)
        self.assertEqual(reset,reset_config(config,42));self.assertEqual(config,original)
        self.assertEqual([len(t['objects']) for t in reset['tables']],[4,5,4,5,4,5,4,5,4])
        for table in reset['tables']:
            self.assertEqual(table['initial_state']['q_left'],HOME_Q)
            self.assertIsNone(table['target']['scale']);self.assertIsNone(table['target']['location_cm'])
        next_run=apply_plan(reset,build_plan([0],prompt='Hold my beer'))
        self.assertEqual(next_run['tables'][0]['objects'],reset['tables'][0]['objects'])
        with self.assertRaises(ValueError):reset_config({'tables':config['tables']+[{'index':9}]},42)

    def test_reset_never_starts_simulation_or_inference(self):
        class Editor:
            def __init__(self):self.calls=[]
            def call(self,name,args):self.calls.append(name)
            def scene(self,expression,description):
                self.calls.append(expression)
                raise RuntimeError('A simulation is running')
        editor=Editor()
        with self.assertRaises(RuntimeError):apply_idle_reset(editor,'/tmp/reset.json')
        self.assertEqual(editor.calls,['begin_task',"maker_arm_fleet.reset('/tmp/reset.json')",'end_task'])
