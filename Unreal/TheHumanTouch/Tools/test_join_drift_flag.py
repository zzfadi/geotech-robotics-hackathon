"""Exercise the launcher's default and opt-in paths without launching Unreal or a camera."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import join_human_touch as join


class DriftLaunchTest(unittest.TestCase):
    def test_calibration_only_and_opt_in(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine = root / "engine"
            (engine / "Build").mkdir(parents=True)
            (engine / "Build/Build.version").write_text(
                json.dumps(
                    {
                        "MajorVersion": 5,
                        "MinorVersion": 8,
                        "PatchVersion": 3,
                        "Changelist": 58210709,
                    }
                )
            )
            args = [
                "join",
                "--local-address",
                "127.0.0.1",
                "--name",
                "test",
                "--engine",
                str(engine),
            ]
            for enabled in (False, True):
                with (
                    self.subTest(enabled=enabled),
                    patch.object(join, "PROJECT", root / "Unreal/TheHumanTouch"),
                    patch.object(join, "editors", return_value=[]),
                    patch.object(join.shutil, "which", return_value="uv"),
                    patch.object(join.subprocess, "Popen") as launch,
                    patch.object(
                        sys, "argv", args + (["--drift-correction"] if enabled else [])
                    ),
                ):
                    launch.return_value.pid = 42
                    join.main()
                    self.assertEqual(launch.call_count, 2 if enabled else 1)
                    self.assertEqual(
                        "-HumanTouchDriftCorrection"
                        in launch.call_args_list[0].args[0],
                        enabled,
                    )
                    if enabled:
                        worker = launch.call_args_list[1].args[0]
                        self.assertIn("--drift-correction", worker)
                        self.assertEqual(worker[worker.index("--editor-pid") + 1], "42")


if __name__ == "__main__":
    unittest.main()
