import unittest

from table10_store import Table10Tests


class Clock:
    def __init__(self):
        self.t = 1000.

    def __call__(self):
        return self.t


class TestTable10Store(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.store = Table10Tests(self.clock)

    def test_requires_online_agent_and_known_action(self):
        with self.assertRaises(ValueError):
            self.store.request({'action': 'move_up'})
        self.store.agent_update({})
        with self.assertRaises(ValueError):
            self.store.request({'action': 'wave'})

    def test_command_lifecycle_and_single_active_motion(self):
        self.store.agent_update({})
        command = self.store.request({'action': 'move_up'})
        self.assertEqual(self.store.agent_update({})['command']['id'], command['id'])
        self.assertIsNone(self.store.agent_update({'claimed': command['id']})['command'])
        self.assertEqual(self.store.public()['command']['status'], 'sent')
        with self.assertRaises(ValueError):
            self.store.request({'action': 'idle'})
        self.store.agent_update({'link': {'test': {'id': command['id'], 'state': 'running', 'progress': .5}}})
        self.assertEqual(self.store.public()['command']['progress'], .5)
        stop = self.store.request({'action': 'stop'})  # always allowed
        self.store.agent_update({'link': {'test': {'id': stop['id'], 'state': 'stopped', 'detail': 'held'}}})
        self.assertEqual(self.store.public()['command']['status'], 'stopped')
        self.store.request({'action': 'idle'})

    def test_agent_goes_offline_and_stale_commands_expire(self):
        self.store.agent_update({})
        self.store.request({'action': 'idle'})
        self.clock.t += 61
        self.store.agent_update({})
        self.assertEqual(self.store.public()['command']['status'], 'error')
        self.clock.t += 6
        self.assertFalse(self.store.public()['agent_online'])

    def test_drive_needs_running_service_expires_and_always_switches_off(self):
        self.store.agent_update({'service': {'running': False}})
        with self.assertRaises(ValueError):
            self.store.request({'action': 'drive_on'})
        self.store.agent_update({'service': {'running': True}})
        out = self.store.request({'action': 'drive_on'})
        self.assertTrue(out['drive']['on'])
        self.assertEqual(self.store.agent_update({'service': {'running': True}})['drive']['seq'], 1)
        self.clock.t += 901
        self.assertFalse(self.store.public()['drive']['on'])
        self.store.agent_update({'service': {'running': True}})
        self.store.request({'action': 'drive_on'})
        self.store.request({'action': 'drive_off'})
        self.assertFalse(self.store.public()['drive']['on'])
        self.assertEqual(self.store.request({'action': 'release'})['release_seq'], 1)


if __name__ == '__main__':
    unittest.main()
