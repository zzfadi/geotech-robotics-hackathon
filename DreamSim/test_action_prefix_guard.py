import unittest
from action_prefix_guard import PrefixGuard

class Prefix(unittest.TestCase):
    def test_stalled_prefixes_stop_after_three_actual_playbacks(self):
        guard=PrefixGuard();execution={'truncated':True,'executed_rows':3}
        self.assertIsNone(guard.observe(execution,0,0))
        self.assertIsNone(guard.observe(execution,0,0))
        self.assertIn('no measurable',guard.observe(execution,0,0))

    def test_real_motion_resets_stall_but_repeated_truncation_still_bounded(self):
        guard=PrefixGuard();execution={'truncated':True}
        for _ in range(5):self.assertIsNone(guard.observe(execution,.001,0))
        self.assertIn('six consecutive',guard.observe(execution,.001,0))
        self.assertIsNone(guard.observe({'truncated':False},0,0))
        self.assertEqual((guard.consecutive,guard.stalled),(0,0))
        self.assertIsNone(guard.observe(execution,0,.002))
        self.assertEqual(guard.stalled,0)

    def test_invalid_progress_cannot_extend_loop(self):
        with self.assertRaises(ValueError):PrefixGuard().observe({'truncated':True},float('nan'),0)

class StandardCheckpoint(unittest.TestCase):
    def test_auto_configures_standard_without_flash_fallback(self):
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import Mock, patch
        from fleet_worker import make_client
        client=Mock();client.wait_for_ik.return_value=True
        configure=Mock()
        modules={'dream':SimpleNamespace(DreamClient=lambda:client),
                 'model':SimpleNamespace(prepare=lambda *a,**k:'official-arm'),
                 'probe':SimpleNamespace(configure=configure)}
        with patch.dict('sys.modules',modules):
            make_client({'checkpoint':'auto','tcp_back':0,'index':0},Path('/tmp/offline'))
        configure.assert_called_once_with(client,'official-arm','dreamlabs/dream-1-beta')
