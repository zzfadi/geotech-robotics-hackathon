"""Device-free checks for snapshot publication and failure cleanup."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import camera_inspect_linux as camera


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.output = Path(self.temporary.name) / 'new snapshot'
        self.device = Path('/dev/v4l/by-id/usb-Test-video-index0')
        self.identity = {'unique_id': self.device.name, 'name': 'Test',
                         'device': str(self.device), 'resolved_device': '/dev/video0'}
        for name, value in [('inspect_device', self.identity), ('shutil.which', '/usr/bin/gst-launch-1.0')]:
            mock = patch.object(camera, name, return_value=value) if '.' not in name else patch(
                'camera_inspect_linux.' + name, return_value=value)
            mock.start()
            self.addCleanup(mock.stop)

    def emit_jpeg(self, command, **kwargs):
        pattern = json.loads(next(p[9:] for p in command if p.startswith('location=')))
        # Minimal SOF fixture: exercises the dimension/framing check, not decoding.
        data = bytes.fromhex('ffd8 ffc0 000b 08 01e0 0280 01 01 11 00 ffd9')
        Path(pattern % 29).write_bytes(data)
        return subprocess.CompletedProcess(command, 0, stderr=b'')

    def assert_no_success(self):
        self.assertFalse((self.output / 'capture-ready.txt').exists())
        self.assertFalse((self.output / 'capture-latest.json').exists())
        self.assertEqual(list(self.output.glob('camera-*.jpg')), [])
        self.assertEqual(list(self.output.glob('.capture-*')), [])
        self.assertIn('error', json.loads((self.output / 'camera-report.json').read_text()))

    @patch('camera_inspect_linux.subprocess.run')
    def test_success_retains_last_frame_and_publishes_contract(self, run):
        run.side_effect = self.emit_jpeg
        report = camera.snapshot(self.device, self.output)
        latest = json.loads((self.output / 'capture-latest.json').read_text())
        self.assertEqual(latest, json.loads((self.output / 'capture-ready.txt').read_text()))
        self.assertEqual(latest, report['cameras'][0]['capture'])
        self.assertEqual((latest['width'], latest['height'], latest['frames_received']), (640, 480, 30))
        self.assertGreaterEqual(latest['receipt_uptime_s'], latest['capture_started_uptime_s'])
        self.assertTrue(Path(latest['image']).is_file())
        self.assertEqual(list(self.output.glob('.capture-*')), [])
        self.assertEqual(run.call_args.kwargs['timeout'], 15)

    @patch('camera_inspect_linux.subprocess.run')
    def test_failed_or_timed_out_capture_never_publishes_ready(self, run):
        for failure in ('returncode', 'timeout', 'truncated'):
            with self.subTest(failure=failure):
                self.output = Path(self.temporary.name) / failure
                def fail(command, **kwargs):
                    result = self.emit_jpeg(command, **kwargs)
                    if failure == 'timeout':
                        raise subprocess.TimeoutExpired(command, 15)
                    if failure == 'truncated':
                        pattern = json.loads(next(p[9:] for p in command if p.startswith('location=')))
                        Path(pattern % 29).write_bytes(b'\xff\xd8partial')
                        return result
                    return subprocess.CompletedProcess(command, 1, stderr=b'device disconnected')
                run.side_effect = fail
                with self.assertRaises((RuntimeError, ValueError, subprocess.TimeoutExpired)):
                    camera.snapshot(self.device, self.output)
                self.assert_no_success()

    @patch('camera_inspect_linux.subprocess.run')
    def test_metadata_failure_removes_ready_and_retained_image(self, run):
        run.side_effect = self.emit_jpeg
        real_write = camera.write_json
        def fail_ready(path, value):
            if path.name == 'capture-ready.txt':
                path.write_text('partial')
                raise OSError('simulated write failure')
            return real_write(path, value)
        with patch.object(camera, 'write_json', side_effect=fail_ready):
            with self.assertRaises(OSError):
                camera.snapshot(self.device, self.output)
        self.assert_no_success()

    @patch('camera_inspect_linux.subprocess.run')
    def test_existing_output_is_preserved(self, run):
        self.output.mkdir()
        previous = self.output / 'capture-ready.txt'
        previous.write_text('prior capture')
        with self.assertRaises(FileExistsError):
            camera.snapshot(self.device, self.output)
        self.assertEqual(previous.read_text(), 'prior capture')
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
