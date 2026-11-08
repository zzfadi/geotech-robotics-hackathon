import copy,math,sys,types,unittest
from unittest.mock import patch
from maker_arm_measured_pose import PoseGate,RuntimeReceiver,infer_virtual_gap,resolve_aliases,LINKS,JOINTS,PREFIX,ALIAS_PREFIX
from maker_arm_model import mounted_frames,ue_pose


def packet(stamp=10,side='left'):
    return dict(table=10,side=side,serial='arm-test',calibration_id='cal-test',feedback_unix=stamp,
                q_urdf_rad=[0]*6,jaw_gap_m=None,joint_names=list(JOINTS),pose_source='measured_sdk_feedback',physical_output_enabled=False)


def poses(q=None,gap=.1):
    out={}
    for n,t in mounted_frames(q or [0]*6,gap,yaw=math.pi).items():
        p,r=ue_pose(t);out[n]={'p':[p[0]+2480,p[1]-40,p[2]+76],'q':r,'scale':[1,1,1]}
    return out


class MeasuredPoseTests(unittest.TestCase):
    def gate(self):return PoseGate({'left':{'serial':'arm-test','calibration_id':'cal-test'}})
    def test_fresh_identity_and_joint_validation(self):
        changes=[{'feedback_unix':9.74},{'feedback_unix':10.01},{'serial':'wrong'},
                 {'calibration_id':'wrong'},{'side':'right'},{'table':1},{'table':True},
                 {'physical_output_enabled':True},{'pose_source':'command_target'},
                 {'q_urdf_rad':[float('nan')]*6},{'q_urdf_rad':[100]*6},
                 {'jaw_gap_m':-.001},{'jaw_gap_m':True},{'joint_names':list(reversed(JOINTS))}]
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ValueError):self.gate().validate({**packet(),**change},10,0)
    def test_rate_limit_and_out_of_order(self):
        g=self.gate();v=g.validate(packet(),10,0);g.commit(v)
        self.assertIsNone(g.validate(packet(10.05),10.05,.05))
        self.assertIsNotNone(g.validate(packet(10.1),10.1,.1))
        with self.assertRaises(ValueError):g.validate(packet(10),10.1,.1)
    def test_unknown_jaw_preserves_verified_virtual_geometry(self):
        for q in ([0]*6,[.1,-1.2,.5,.7,.2,-.3]):
            for gap in (0,.04,.104825):
                p=poses(q,gap)
                self.assertAlmostEqual(infer_virtual_gap(p['link_007'],p['gripper_left'],p['gripper_right']),gap,places=9)
    def test_invalid_virtual_jaw_geometry_rejected(self):
        p=poses();p['gripper_left']['p'][0]+=.1
        with self.assertRaises(ValueError):infer_virtual_gap(p['link_007'],p['gripper_left'],p['gripper_right'])
        p=poses();p['gripper_right']['scale'][0]=2
        with self.assertRaises(ValueError):infer_virtual_gap(p['link_007'],p['gripper_left'],p['gripper_right'])
    def test_exact_namespace_and_unique_binding(self):
        pairs=[(PREFIX+'MakerArm_left_'+n,object()) for n in LINKS]
        original=[('MakerArm_left_'+n,object()) for n in LINKS]
        aliases=resolve_aliases(pairs+original,'left')
        self.assertEqual(set(aliases),{ALIAS_PREFIX+'MakerArm_left_'+n for n in LINKS})
        self.assertEqual(set(aliases.values()),{a for n,a in pairs})
        with self.assertRaises(ValueError):resolve_aliases(pairs+pairs[:1],'left')
        with self.assertRaises(ValueError):resolve_aliases(original,'left')
    def test_editor_world_is_rejected(self):
        fake=types.SimpleNamespace(SystemLibrary=types.SimpleNamespace(is_valid=lambda w:True),UnrealEditorSubsystem=object,
                                   get_editor_subsystem=lambda cls:types.SimpleNamespace(get_game_world=lambda:None))
        with patch.dict(sys.modules,{'unreal':fake}),self.assertRaises(RuntimeError):
            RuntimeReceiver({'left':{'serial':'arm-test','calibration_id':'cal-test'}},object())
    def test_receiver_passes_preserved_gap_and_never_other_arm(self):
        world=object();p=poses(gap=.075)
        class Actor:
            def __init__(self,n):self.n=n
            def get_actor_label(self):return PREFIX+'MakerArm_left_'+self.n
            def get_actor_transform(self):
                t=p[self.n];return types.SimpleNamespace(translation=types.SimpleNamespace(**dict(zip('xyz',t['p']))),rotation=types.SimpleNamespace(**dict(zip('xyzw',t['q']))),scale3d=types.SimpleNamespace(**dict(zip('xyz',t['scale']))))
        actors=[Actor(n) for n in LINKS]
        fake=types.SimpleNamespace(Actor=Actor,SystemLibrary=types.SimpleNamespace(is_valid=lambda w:True),UnrealEditorSubsystem=object,
                                   get_editor_subsystem=lambda cls:types.SimpleNamespace(get_game_world=lambda:world),
                                   GameplayStatics=types.SimpleNamespace(get_all_actors_of_class=lambda w,c:actors))
        calls=[]
        def set_pose(index,side,q,gap,existing):calls.append((index,side,gap,existing));return 0
        with patch.dict(sys.modules,{'unreal':fake,'maker_arm_fleet':types.SimpleNamespace(set_pose=set_pose)}):
            receiver=RuntimeReceiver({'left':{'serial':'arm-test','calibration_id':'cal-test'}},world)
            result=receiver.apply(packet(),now=10,monotonic=0)
            self.assertFalse(result['jaw_measured']);self.assertEqual(result['moved_links'],0)
            self.assertAlmostEqual(calls[0][2],.075);self.assertEqual(calls[0][:2],(9,'left'))
            self.assertFalse(receiver.apply(packet(10.01),now=10.01,monotonic=.01)['applied'])
            self.assertEqual(len(calls),1)
            p['base_link']['p'][0]+=1
            with self.assertRaises(ValueError):receiver.apply(packet(10.2),now=10.2,monotonic=.2)
            self.assertEqual(len(calls),1)
            receiver.close()
            with self.assertRaises(RuntimeError):receiver.apply(packet(10.3),now=10.3,monotonic=.3)

if __name__=='__main__':unittest.main()
