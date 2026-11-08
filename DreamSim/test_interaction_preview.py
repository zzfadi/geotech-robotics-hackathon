"""Separate Table 10 upload must never become fleet table 9 or create inference."""
import json
import tempfile
import unittest
from pathlib import Path
from fleet_worker import IdlePreview


class PreviewTests(unittest.TestCase):
    def test_routes_separate_camera_and_deduplicates_capture(self):
        class Server:
            def __init__(self): self.calls = []
            def api(self, path, data): self.calls.append((path, data))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            frame = folder/'frame.jpg'
            frame.write_bytes(b'\xff\xd8example\xff\xd9')
            packet = {'running': True, 'frames': {'T00': str(frame), 'InteractionT10': str(frame)},
                      'frame_times': {'T00': 100., 'InteractionT10': 101.}}
            (folder/'preview.json').write_text(json.dumps(packet))
            server = Server()
            preview = IdlePreview(None, folder, server)
            preview.publish()
            self.assertEqual(len(server.calls), 2)
            self.assertEqual(set(server.calls[0][1]['frames']), {'T00'})
            self.assertEqual(server.calls[1][0], '/api/interaction/worker/preview')
            self.assertEqual(server.calls[1][1]['captured_unix'], 101.)
            self.assertTrue(server.calls[1][1]['frame'])
            preview.publish()
            self.assertEqual(len(server.calls), 2)

    def test_stalled_capture_callback_requests_restart(self):
        from unittest.mock import patch
        class Server:
            def api(self,*args):pass
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            (folder/'preview.json').write_text(json.dumps({'running':True,'sequence':4,'frames':{}}))
            preview=IdlePreview(None,folder,Server());preview.running=True
            with patch('fleet_worker.time.monotonic',return_value=100):preview.publish()
            with patch('fleet_worker.time.monotonic',return_value=111):
                with self.assertRaisesRegex(RuntimeError,'callback stalled'):preview.publish()
            self.assertFalse(preview.running)
            self.assertEqual(preview.retry_after,112)

    def test_advancing_callback_is_not_restarted(self):
        from unittest.mock import patch
        class Server:
            def api(self,*args):pass
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);preview=IdlePreview(None,folder,Server());preview.running=True
            for seq,now in [(1,100),(2,111)]:
                (folder/'preview.json').write_text(json.dumps({'running':True,'sequence':seq,'frames':{}}))
                with patch('fleet_worker.time.monotonic',return_value=now):preview.publish()
            self.assertTrue(preview.running)
