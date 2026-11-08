import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
import numpy as np
from Interaction.mirror import MirrorConsumer, write_mailbox


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.cal={'side':'left','serial':'offline-serial','id':'fixture','validated':True,
                  'validation_evidence':'synthetic fixture only','sdk_to_urdf_matrix':np.eye(6).tolist(),
                  'urdf_offset_rad':[0]*6}
        self.state={'side':'left','serial':'offline-serial','calibration_id':'fixture',
                    'feedback_unix':10.,'q_sdk_rad':[.1,-1,1,-.3,.2,.1]}
        self.status={'table':10,'side':'left','host':'offline-host','session_id':'a'*32,
                     'controller_online':True,'state':self.state,'stop_requested':True}
        self.consumer=MirrorConsumer('https://example.com',session_id='a'*32,
                                      host='offline-host',calibration=self.cal,clock=lambda:10.1)

    def test_actual_measurement_only_and_pinned_identity(self):
        self.state['target_sdk_rad']=[99]*6
        pose=self.consumer.convert(self.status)
        self.assertEqual(pose['q_urdf_rad'],self.state['q_sdk_rad'])
        self.assertIsNone(pose['jaw_gap_m'])
        for change in ({'session_id':'b'*32},{'host':'other'},{'side':'right'},
                       {'controller_online':False},{'state':None}):
            with self.assertRaises(ValueError):self.consumer.convert({**self.status,**change})

    def test_transport_packet_matches_unreal_pose_gate(self):
        from maker_arm_measured_pose import PoseGate
        packet=self.consumer.convert(self.status)
        gate=PoseGate({'left':{'serial':'offline-serial','calibration_id':'fixture'}})
        accepted=gate.validate(packet,10.1,100.)
        self.assertEqual(accepted['side'],'left')
        self.assertIsNone(accepted['gap'])
        gate.commit(accepted)
        with self.assertRaises(ValueError):gate.validate(packet,10.1,101.)

    def test_expiry_and_reordering(self):
        self.consumer.convert(self.status)
        for stamp in (9.99,9.,11.):
            with self.assertRaises(ValueError):
                self.consumer.convert({**self.status,'state':{**self.state,'feedback_unix':stamp}})

    def test_network_failure_clears_packet_without_retry(self):
        self.consumer.opener.open=Mock(side_effect=OSError('sensitive detail'))
        result=self.consumer.poll_once()
        self.assertEqual(result['status'],'unavailable');self.assertIsNone(result['packet'])
        self.assertNotIn('sensitive',json.dumps(result));self.consumer.opener.open.assert_called_once()

    def test_fresh_response_and_atomic_mailbox_replacement(self):
        response=Mock();response.read.return_value=json.dumps(self.status).encode()
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        self.consumer.opener.open=Mock(return_value=response)
        envelope=self.consumer.poll_once();self.assertEqual(envelope['status'],'fresh')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'mirror.json';write_mailbox(path,envelope)
            self.assertEqual(json.loads(path.read_text())['packet']['pose_source'],'measured_sdk_feedback')
            unavailable={**envelope,'status':'unavailable','packet':None}
            write_mailbox(path,unavailable)
            self.assertIsNone(json.loads(path.read_text())['packet'])
            self.assertEqual(list(Path(directory).iterdir()),[path])

if __name__=='__main__':unittest.main()
