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
