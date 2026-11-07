"""Safe Git actions for a designated publisher and a pull-only session peer.

This is an operator workflow, never an Agent Chat tool. Callers save and close
editors before reader synchronization; no stash/reset/force push is performed.
"""
from pathlib import Path
import os
import shutil
import subprocess


def git_executable():
    executable = os.environ.get('COPRESENCE_GIT') or shutil.which('git')
    if not executable or not Path(executable).is_file():
        raise RuntimeError('Git is unavailable; add it to PATH or set COPRESENCE_GIT to its executable')
    return executable


def git(root, *args):
    return subprocess.check_output([git_executable(),'-C',str(root),*args],text=True).strip()


def sync_reader(root, *, editors_running, runtime=None):
    if editors_running:
        raise RuntimeError('Close the reader editor before pulling assets from main')
    root=Path(root)
    if git(root,'status','--porcelain'):
        raise RuntimeError('Reader checkout has local changes; preserve them before syncing')
    # Check the actual Git subcommand, including Git's own executable search
    # path, before fetch/merge can change the checkout.
    try:
        git(root, 'lfs', 'version')
    except subprocess.CalledProcessError as error:
        raise RuntimeError('Git LFS is unavailable; add git-lfs to PATH before reader synchronization') from error
    git(root,'fetch','origin','main')
    # Neither local commits nor divergence may be silently discarded.
    if int(git(root,'rev-list','--count','origin/main..HEAD')):
        raise RuntimeError('Reader has unpublished commits; use the writer workflow to reconcile them')
    git(root,'merge','--ff-only','origin/main')
    git(root,'lfs','pull','origin','main')
    revision = git(root,'rev-parse','HEAD')
    if runtime is not None and Path(runtime).resolve() != root.resolve():
        from copresence_runtime_sync import synchronize
        files = subprocess.check_output([git_executable(),'-C',str(root),'ls-files','-z']).decode().split('\0')
        synchronize(root, runtime, [name for name in files if name], editors_running=False, revision=revision)
    return revision


def push_writer(root, *, host, writer, persisted):
    if host != writer:
        raise RuntimeError('This host is the pull-only peer, not the Git publisher')
    if not persisted:
        raise RuntimeError('Verify level and content-asset persistence before publishing')
    # Only already-reviewed commits are published, never automatic git add -A.
    git(root,'push','origin','HEAD:main')
    return git(root,'rev-parse','HEAD')
