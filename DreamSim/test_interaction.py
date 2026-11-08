import tempfile
import unittest
from pathlib import Path

from interaction_store import InteractionStore


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.now = 100.
        self.relay = InteractionStore(Path(self.temp.name), lambda: self.now)
        self.session = self.relay.begin({'host': 'test-controller','side':'left'})['session_id']
        self.state = dict(session_id=self.session, side='left', mode='monitor', local_deadman=False,
                          feedback_unix=self.now, calibration_id=None, serial='test-adapter', faults=[False]*6,
                          **{key: [0.]*6 for key in ('q_sdk_rad', 'dq_sdk_rad_s', 'torque_nm', 'temperature_c')})
        self.event = dict(version=1, session_id=self.session, side='left', table=10, source_id='test-hand',
                          source_kind='hand', seq=0, captured_unix=self.now,
                          link='link_007', frame='urdf_base_rh', point_m=[.3,0,.2], force_n=[1,0,0])

    def tearDown(self):
        self.temp.cleanup()

    def test_roles_are_distinct_and_no_credentials_in_status(self):
        self.assertNotEqual(*self.relay.tokens.values())
        token = 'Bearer '+self.relay.tokens['producer']
        self.assertTrue(self.relay.authorized('producer',token))
        self.assertFalse(self.relay.authorized('controller',token))
        self.assertNotIn(self.relay.tokens['producer'],str(self.relay.public()))

    def test_monitor_delivery_never_enables_and_contact_expires(self):
        self.relay.heartbeat(self.state)
        self.relay.publish(self.event)
        received = self.relay.next_contact(self.session)
        self.assertEqual(received['contact']['force_n'],[1,0,0])
        self.assertFalse(received['hardware_enable'])
        self.now += .101
        self.assertIsNone(self.relay.next_contact(self.session)['contact'])

    def test_stop_latches_and_heartbeat_cannot_clear_it(self):
        self.relay.heartbeat(self.state)
        self.relay.publish(self.event)
        self.relay.stop()
        self.relay.heartbeat(self.state)
        self.assertTrue(self.relay.public()['stop_requested'])
        self.assertIsNone(self.relay.next_contact(self.session)['contact'])

    def test_replay_wrong_frame_nonfinite_and_excess_force_rejected(self):
        self.relay.publish(self.event)
        with self.assertRaises(ValueError): self.relay.publish(self.event)
        for changes in ({'frame':'ue_world'}, {'point_m':[float('nan'),0,0]},
                        {'force_n':[5,1,0]}, {'table':9}, {'captured_unix':99.},
                        {'session_id':'old'}, {'side':'right'}, {'seq':True}, {'version':True}, {'link':'link_001'}, {'point_m':[1.6,0,0]}, {'captured_unix':100.001}):
            with self.assertRaises(ValueError): self.relay.publish({**self.event,'seq':1,**changes})

    def test_controller_and_feedback_expiration_prevent_delivery(self):
        self.relay.heartbeat(self.state)
        self.now += .26
        self.relay.publish({**self.event,'captured_unix':self.now})
        self.assertFalse(self.relay.public()['controller_online'])
        self.assertIsNone(self.relay.next_contact(self.session)['contact'])
        with self.assertRaises(ValueError): self.relay.heartbeat(self.state)

    def test_session_replacement_invalidates_old_contacts(self):
        self.relay.heartbeat(self.state)
        with self.assertRaises(ValueError): self.relay.begin({'host':'other','side':'left'})
        self.now += 1.1
        new = self.relay.begin({'host':'other','side':'left'})['session_id']
        self.assertNotEqual(new,self.session)
        with self.assertRaises(ValueError): self.relay.next_contact(self.session)


if __name__ == '__main__':
    unittest.main()
