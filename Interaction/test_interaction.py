import copy,math,unittest
from types import SimpleNamespace as NS
import numpy as np
from Interaction.protocol import validate_contact,ContactLease
from Interaction.control import point_jacobian,contact_torque,bounded_admittance,calibrated_plan,ue_contact_to_urdf,EFFORT_NM,RATE_RAD_S
from Interaction.sdk_adapter import sample_connected_arm
from maker_arm_model import frames

EVENT={'version':1,'table':10,'side':'left','session_id':'a'*32,'source_id':'hand-L','source_kind':'hand',
       'seq':0,'captured_unix':10.,'link':'link_007','frame':'urdf_base_rh','point_m':[.1,.2,.3],'force_n':[1,0,0]}
class Protocol(unittest.TestCase):
 def test_expiry_replay_and_cross_arm(self):
  lease=ContactLease('a'*32,'left');lease.accept(EVENT,10.01)
  self.assertIsNotNone(lease.current(10.09));self.assertIsNone(lease.current(10.11))
  with self.assertRaises(ValueError):lease.accept(EVENT,10.02)
  for change in ({'side':'right'},{'session_id':'b'*32}):
   with self.assertRaises(ValueError):lease.accept({**EVENT,**change,'seq':1},10.02)
 def test_reject_bad_force_frame_time(self):
  for change in ({'force_n':[6,0,0]},{'force_n':[float('nan'),0,0]},{'frame':'ue_world_cm'},
                 {'seq':True},{'captured_unix':9},{'side':None},{'link':'gripper_left'}):
   with self.assertRaises(ValueError):validate_contact({**EVENT,**change},10)
class Control(unittest.TestCase):
 def test_centimetres_handedness_and_force_units(self):
  t=np.diag([1.,-1.,1.,1.]);t[:3,3]=[24.8,0,.76]
  point,force=ue_contact_to_urdf([2490,20,86],[1,2,3],t)
  np.testing.assert_allclose(point,[.1,-.2,.1]);np.testing.assert_allclose(force,[1,-2,3])
  with self.assertRaises(ValueError):ue_contact_to_urdf([0,0,0],[1,0,0],np.eye(4))
 def test_point_jacobian_matches_link_attached_finite_difference(self):
  q=[.1,-1.,1.,-.3,.2,.1];pose=np.array(frames(q,0)['link_007']);local=np.array([.01,.02,.03,1]);point=(pose@local)[:3]
  jac=point_jacobian(q,'link_007',point.tolist())
  for i in range(6):
   plus=q.copy();minus=q.copy();plus[i]+=1e-6;minus[i]-=1e-6
   numeric=((np.array(frames(plus,0)['link_007'])@local)[:3]-(np.array(frames(minus,0)['link_007'])@local)[:3])/2e-6
   np.testing.assert_allclose(jac[:,i],numeric,atol=1e-8)
  force=np.array([1.,2.,-1.]);delta=np.array([.01]*6)
  self.assertAlmostEqual(float(contact_torque(q,'link_007',point.tolist(),force.tolist())@delta),float(force@(jac@delta)))
  np.testing.assert_allclose(point_jacobian(q,'base_link',[0,0,0]),0)
 def test_bounded_target_effort_rate_and_limits(self):
  result=bounded_admittance([0]*6,[0]*6,[100]*6,[0]*6,.04,[[-1,1]]*6,[.5]*6)
  self.assertTrue(np.all(np.array(result['virtual_torque_nm'])+.5<=EFFORT_NM+1e-12))
  self.assertTrue(np.all(abs(np.array(result['target_sdk_rad']))<=RATE_RAD_S*.04+1e-12))
  for change in ({'dt':.2},{'q_sdk':[2]*6},{'dq_sdk':[1]*6},{'measured_torque':[9]*6}):
   args=dict(q_sdk=[0]*6,dq_sdk=[0]*6,tau_sdk=[0]*6,previous_target=[0]*6,dt=.04,limits=[[-1,1]]*6,measured_torque=[0]*6);args.update(change)
   with self.assertRaises(ValueError):bounded_admittance(**args)
 def test_missing_calibration_never_emits_hardware_command(self):
  with self.assertRaises(ValueError):calibrated_plan(EVENT,{},None,10,[0]*6)
 def test_registered_plan_is_preview_only_and_expiry_holds(self):
  q=[.1,-1.,1.,-.3,.2,.1]
  state={'side':'left','serial':'bench','calibration_id':'synthetic-test','local_deadman':True,'feedback_unix':10,
         'faults':[False]*6,'temperature_c':[30]*6,'q_sdk_rad':q,'dq_sdk_rad_s':[0]*6,'torque_nm':[0]*6,'limits_sdk_rad':[[-3,3]]*6}
  calibration={'side':'left','serial':'bench','id':'synthetic-test','validated':True,'validation_evidence':'synthetic fixture ONLY',
               'sdk_to_urdf_matrix':np.eye(6).tolist(),'urdf_offset_rad':[0]*6}
  result=calibrated_plan(EVENT,state,calibration,10,q)
  self.assertFalse(result['physical_output_enabled'])
  self.assertEqual(calibrated_plan(None,state,calibration,10,q)['action'],'retain_guarded_hold')
  with self.assertRaises(ValueError):calibrated_plan({**EVENT,'side':'right'},state,calibration,10,q)
class Adapter(unittest.TestCase):
 def test_actual_sdk_attribute_contract_no_enable_or_refresh(self):
  joints=[NS(motor_id=i+1,lo=-3,hi=3,direction=-1 if i==0 else 1) for i in range(7)]
  motors=[NS(feedback=NS(velocity=.1,torque=.2,temperature=30,warning_flag=False,fault_flag=False),feedback_age=.01,fault_latched=False)for _ in range(7)]
  arm=NS(config=NS(joints=joints),motors=motors,get_joint_positions=lambda:[.2]*7,state=NS(name='CONNECTED'))
  state=sample_connected_arm(arm,10,serial='bench',side='left')
  self.assertEqual(len(state['q_sdk_rad']),6);self.assertEqual(state['dq_sdk_rad_s'][0],-.1)
  self.assertEqual(state['torque_nm'][0],-.2);self.assertFalse(state['physical_output_enabled'])
  motors[2].feedback_age=1
  with self.assertRaises(ValueError):sample_connected_arm(arm,10,serial='bench',side='left')

class FeedbackPose(unittest.TestCase):
 def test_measured_pose_requires_mapping_freshness_and_side(self):
  from Interaction.feedback_pose import measured_feedback_pose
  q=[.1,-1.,1.,-.3,.2,.1]
  state={'side':'left','serial':'bench','calibration_id':'synthetic','feedback_unix':10,'q_sdk_rad':q,
         'target_sdk_rad':[99]*6}
  mapping=np.eye(6);mapping[0,0]=-1
  calibration={'side':'left','serial':'bench','id':'synthetic','validated':True,'validation_evidence':'test only',
               'sdk_to_urdf_matrix':mapping.tolist(),'urdf_offset_rad':[.2,0,0,0,0,0]}
  pose=measured_feedback_pose(state,calibration,10.1)
  np.testing.assert_allclose(pose['q_urdf_rad'],q)
  self.assertIsNone(pose['jaw_gap_m']);self.assertEqual(pose['pose_source'],'measured_sdk_feedback')
  self.assertFalse(pose['physical_output_enabled'])
  for changes in ({'side':'right'},{'serial':'other'},{'feedback_unix':9},{'feedback_unix':10.2},{'q_sdk_rad':[99]*6}):
   with self.assertRaises(ValueError):measured_feedback_pose({**state,**changes},calibration,10.1)
  with self.assertRaises(ValueError):measured_feedback_pose(state,None,10.1)

class EndToEndSchema(unittest.TestCase):
 def test_sampler_relay_measured_pose(self):
  import tempfile
  from pathlib import Path
  from DreamSim.interaction_store import InteractionStore
  from Interaction.feedback_pose import measured_feedback_pose
  q=[.1,-1.,1.,-.3,.2,.1]
  joints=[NS(motor_id=i+1,lo=-3,hi=3,direction=1) for i in range(7)]
  motors=[NS(feedback=NS(velocity=0,torque=0,temperature=30,warning_flag=False,fault_flag=False),feedback_age=.01,fault_latched=False)for _ in range(7)]
  arm=NS(config=NS(joints=joints),motors=motors,get_joint_positions=lambda:q+[0],state=NS(name='CONNECTED'))
  sample=sample_connected_arm(arm,10,serial='test-adapter',side='left',calibration_id='synthetic')
  with tempfile.TemporaryDirectory() as folder:
   relay=InteractionStore(Path(folder),lambda:10)
   session=relay.begin({'host':'offline-test','side':'left'})['session_id']
   relay.heartbeat({**sample,'session_id':session,'mode':'monitor'})
   state=relay.public()['state']
   cal={'side':'left','serial':'test-adapter','id':'synthetic','validated':True,'validation_evidence':'synthetic only',
        'sdk_to_urdf_matrix':np.eye(6).tolist(),'urdf_offset_rad':[0]*6}
   pose=measured_feedback_pose(state,cal,10)
   self.assertEqual(pose['q_urdf_rad'],q)
   self.assertEqual(state['temperature_c'],[30]*6)
   self.assertIsNone(pose['jaw_gap_m'])
   packet={**EVENT,'session_id':session}
   relay.publish(packet)
   self.assertEqual(validate_contact(relay.next_contact(session)['contact'],10)['version'],1)
