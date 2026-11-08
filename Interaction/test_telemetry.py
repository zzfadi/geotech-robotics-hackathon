import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from DreamSim.interaction_store import InteractionStore
from Interaction.telemetry import TelemetryPublisher, RelayError, NoRedirect


def arm():
    return NS(config=NS(joints=[NS(motor_id=i+1,lo=-3,hi=3,direction=1) for i in range(7)]),
              motors=[NS(feedback=NS(velocity=0,torque=0,temperature=30,warning_flag=False,fault_flag=False),
                         feedback_age=.01,fault_latched=False) for _ in range(7)],
              get_joint_positions=lambda:[.1]*7,state=NS(name='CONNECTED'))


class TelemetryTests(unittest.TestCase):
    def publisher(self):
        return TelemetryPublisher('https://luna2.tail6864ac.ts.net', 'test-only',
                                  host='offline-test',side='left',serial='test-serial',clock=lambda:10)

    def test_sampler_to_authenticated_request_to_relay(self):
        p=self.publisher()
        with tempfile.TemporaryDirectory() as folder:
            relay=InteractionStore(Path(folder),lambda:10)
            relay.tokens['controller']='test-only'
            def deliver(req, timeout):
                self.assertLessEqual(timeout,.25)
                self.assertTrue(relay.authorized('controller',req.get_header('Authorization')))
                data=json.loads(req.data)
                result=relay.begin(data) if req.full_url.endswith('/session') else relay.heartbeat(data)
                response=Mock(); response.read.return_value=json.dumps(result).encode()
                response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
                return response
            p.opener.open=deliver
            p.register()
            self.assertTrue(p.publish_once(arm())['delivered'])
            self.assertEqual(relay.public()['state']['serial'],'test-serial')
            self.assertEqual(relay.public()['state']['mode'],'monitor')
            relay.stop()
            self.assertTrue(p.publish_once(arm())['stop_requested'])
            self.assertTrue(relay.stop_requested)

    def test_no_automatic_registration_retry_or_stale_delivery(self):
        p=self.publisher();p.opener.open=Mock(side_effect=OSError('private detail'))
        with self.assertRaises(RelayError):p.publish_once(arm())
        p.opener.open.assert_not_called()
        p.session_id='a'*32
        with self.assertRaisesRegex(RelayError,'no retry'):p.publish_once(arm())
        self.assertEqual(p.opener.open.call_count,1)
        stale=arm();stale.motors[0].feedback_age=1
        with self.assertRaises(ValueError):p.publish_once(stale)
        self.assertEqual(p.opener.open.call_count,1)

    def test_https_origin_and_no_redirect(self):
        for url in ('http://luna2.tail6864ac.ts.net','https://user:secret@example.com',
                    'https://example.com/path','https://example.com?token=secret'):
            with self.assertRaises(ValueError):
                TelemetryPublisher(url,'test',host='test',side='left',serial='test')
        self.assertIsNone(NoRedirect().redirect_request(None,None,302,'',{},'https://elsewhere.example'))

if __name__=='__main__':unittest.main()
