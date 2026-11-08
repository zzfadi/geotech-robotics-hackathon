import tempfile
import tomllib
import unittest
from pathlib import Path

from quest_passthrough_config import prepare_runtime


class RuntimeProfileTest(unittest.TestCase):
    def test_existing_profile_preserves_other_tables_and_comments(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "runtime.toml"
            p.write_text('[streaming] # Quest\nbitrate=24\npassthrough_enabled = false # room\n'
                         '[spatial]\npassthrough_enabled = false\n')
            prepare_runtime(p)
            body = p.read_text()
            values = tomllib.loads(body)
            self.assertEqual(values["streaming"], dict(bitrate=24, passthrough_enabled=True,
                                                        app_alpha_blend_passthrough=True))
            self.assertFalse(values["spatial"]["passthrough_enabled"])
            self.assertIn("true # room", body)
            inode = p.stat().st_ino
            prepare_runtime(p)
            self.assertEqual(p.stat().st_ino, inode)

    def test_first_launch_and_profile_without_streaming_table(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "new/runtime.toml"
            prepare_runtime(p)
            self.assertTrue(tomllib.loads(p.read_text())["streaming"]["passthrough_enabled"])
            p.write_text('[general]\nruntime_enabled=true')
            prepare_runtime(p)
            self.assertTrue(tomllib.loads(p.read_text())["general"]["runtime_enabled"])

    def test_invalid_profile_is_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder) / "runtime.toml"
            p.write_text("[streaming\n")
            with self.assertRaises(ValueError):
                prepare_runtime(p)
            self.assertEqual(p.read_text(), "[streaming\n")
