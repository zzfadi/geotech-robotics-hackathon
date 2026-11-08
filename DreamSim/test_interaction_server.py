import unittest
import test_fleet_server as fixture


class InteractionServer(unittest.TestCase):
    setUp = fixture.FleetServer.setUp
    tearDown = fixture.FleetServer.tearDown
    request = fixture.FleetServer.request
    status = fixture.FleetServer.status

    def test_browser_cannot_register_controller_or_publish_contacts(self):
        self.assertEqual(self.status('/api/interaction/controller/session', {'host':'test','side':'left'}),401)
        self.assertEqual(self.status('/api/interaction/contact', {}),401)
        self.assertEqual(self.status('/api/interaction/controller/contact'),401)
        self.assertEqual(self.status('/api/interaction/controller/session', {'host':'test','side':'left'},self.token),401)

    def test_controller_token_is_not_producer_token(self):
        tokens=self.server.store.interaction.tokens
        self.assertEqual(self.status('/api/interaction/controller/session', {'host':'test','side':'left'},tokens['producer']),401)
        session=self.request('/api/interaction/controller/session', {'host':'test','side':'left'},tokens['controller'])
        self.assertEqual(session['table'],10)
        self.assertFalse(session['physical_enable_available'])
        self.assertEqual(self.status('/api/interaction/contact', {},tokens['controller']),401)

    def test_public_stop_cannot_enable_and_origin_guard_applies(self):
        self.assertEqual(self.status('/api/interaction/stop', {},origin='https://untrusted.example'),403)
        self.assertTrue(self.request('/api/interaction/stop',{})['stop_requested'])
        state=self.request('/api/interaction/status')
        self.assertFalse(state['controller_online'])
        self.assertFalse(state['hardware_enable_available'])
        self.assertFalse(state['physical_motion_verified'])


if __name__ == '__main__':
    unittest.main()
