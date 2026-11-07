"""Exercise Git safety using throwaway repositories and a read-only fake editor."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import checkpoint as cp


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.repo = root / 'repo'
        self.remote = root / 'remote.git'
        subprocess.run(['git', 'init', '--bare', '-q', str(self.remote)], check=True)
        subprocess.run(['git', 'init', '-q', '-b', 'main', str(self.repo)], check=True)
        self.addCleanup(patch.stopall)
        patch.object(cp, 'REPO', self.repo).start()
        cp.git('config', 'user.email', 'checkpoint-test@example.invalid')
        cp.git('config', 'user.name', 'Checkpoint Test')
        (self.repo / 'README.md').write_text('baseline')
        cp.git('add', 'README.md')
        cp.git('commit', '-qm', 'baseline')
        cp.git('remote', 'add', 'origin', str(self.remote))
        cp.git('push', '-qu', 'origin', 'main')
        self.content = self.repo / cp.POLICY['content_root']
        self.content.mkdir(parents=True)
        patch.object(cp, 'hub_idle', lambda: None).start()
        info = dict(agent=cp.POLICY['host_agent'], session=cp.POLICY['session'],
                    task=None, animation=False, maps=[], content=[], pending=[], operation={'state': 'idle'})
        client = types.SimpleNamespace(copresence=lambda *args: {'value': repr(info), 'error': None})
        patch.dict(sys.modules, {'copresence_mcp': types.SimpleNamespace(UnrealMcp=lambda url: client)}).start()

    def test_unchanged_does_not_commit(self):
        head = cp.git('rev-parse', 'HEAD')
        self.assertEqual(cp.checkpoint()['state'], 'unchanged')
        self.assertEqual(cp.git('rev-parse', 'HEAD'), head)

    def test_pushes_only_assets_preserving_unrelated_work(self):
        (self.content / 'Map.umap').write_bytes(b'test asset')
        (self.repo / 'README.md').write_text('unrelated unfinished edit')
        result = cp.checkpoint()
        self.assertEqual(result['state'], 'pushed')
        self.assertEqual(cp.git('rev-parse', 'HEAD'), cp.git('rev-parse', 'origin/main'))
        self.assertEqual(cp.git('diff', '--name-only'), 'README.md')

    def test_staged_work_is_not_consumed(self):
        (self.repo / 'README.md').write_text('someone else staged this')
        cp.git('add', 'README.md')
        with self.assertRaisesRegex(RuntimeError, 'staged'):
            cp.checkpoint()
        self.assertEqual(cp.git('diff', '--cached', '--name-only'), 'README.md')

    def test_rejects_non_asset_content(self):
        (self.content / 'secret.txt').write_text('do not stage')
        with self.assertRaisesRegex(RuntimeError, 'non-asset'):
            cp.checkpoint()
        self.assertEqual(cp.git('diff', '--cached', '--name-only'), '')

    def test_failed_push_retains_commit_and_retry_succeeds(self):
        (self.content / 'Map.umap').write_bytes(b'test asset')
        hook = self.remote / 'hooks/pre-receive'
        hook.write_text('#!/bin/sh\nexit 1\n'); hook.chmod(0o755)
        with self.assertRaises(subprocess.CalledProcessError):
            cp.checkpoint()
        committed = cp.git('rev-parse', 'HEAD')
        self.assertEqual(cp.git('rev-list', '--count', 'origin/main..HEAD'), '1')
        hook.unlink()
        self.assertEqual(cp.checkpoint()['state'], 'pushed')
        self.assertEqual(cp.git('rev-parse', 'origin/main'), committed)

    def test_dry_run_never_stages_or_commits(self):
        (self.content / 'Map.umap').write_bytes(b'test asset')
        head = cp.git('rev-parse', 'HEAD')
        self.assertEqual(cp.checkpoint(True)['state'], 'ready')
        self.assertEqual(cp.git('rev-parse', 'HEAD'), head)
        self.assertEqual(cp.git('diff', '--cached', '--name-only'), '')


if __name__ == '__main__':
    unittest.main()
