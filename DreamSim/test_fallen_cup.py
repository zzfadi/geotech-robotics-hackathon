import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from fleet_worker import target_cup_fell, TableRun

CUP='MakerStation_Prop_Cup_03'

def status(height, **overrides):
    prop={'height_above_start_cm':height,'physics_simulated':True,'gravity_enabled':True,'attached_to':None}
    prop.update(overrides)
    return {'sampled_unix':100.,'props':{CUP:prop}}

class FallenCup(unittest.TestCase):
    def test_only_fresh_physical_fall_is_terminal(self):
        self.assertTrue(target_cup_fell(status(-77),CUP,100))
        for height in (-20,-12,0,10,float('nan'),float('-inf')):
            self.assertFalse(target_cup_fell(status(height),CUP,100))
        for override in ({'physics_simulated':False},{'gravity_enabled':False},{'attached_to':'gripper'}):
            self.assertFalse(target_cup_fell(status(-77,**override),CUP,100))
        self.assertFalse(target_cup_fell(status(-77),CUP,103))
        self.assertFalse(target_cup_fell(status(-77),CUP,99))
        self.assertFalse(target_cup_fell(status(-77),'unknown-prop',100))

    def test_failed_episode_does_not_request_more_inference_and_releases_session(self):
        class Client:
            released=False
            def reset_episode(self):self.released=True
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'T00').mkdir()
            table=status(-77);table.pop('sampled_unix')
            (root/'fleet.json').write_text(json.dumps({'phase':'ready','sampled_unix':time.time(),'tables':{'T00':table}}))
            variant={'index':0,'name':'cup','prompt':'lift','checkpoint':'test','position_ik':True,
                     'pad_contacts':True,'action_rows':12,'cycles':16,'target':{'label':CUP},'objective':None}
            client=Client();run=TableRun(variant,root,SimpleNamespace(dry_run=False),client)
            run.fresh=lambda _: self.fail('No new observation should be requested after a confirmed fall')
            run.infer=lambda _: self.fail('No paid request after a confirmed fall')
            result=run()
            self.assertEqual(result['outcome'],'failed');self.assertEqual(result['calls'],0)
            self.assertIn('fell more than 20 cm',result['stopped_reason'])
            self.assertTrue(client.released)
