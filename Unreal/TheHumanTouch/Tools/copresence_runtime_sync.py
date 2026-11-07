"""Deploy reviewed Git project files to a closed, separate Unreal runtime.

Local runtime edits are conflicts, never silently discarded. The runtime project
file and generated engine/build state are host-owned and are not synchronized.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil

SCOPES = {'Content', 'Config', 'Plugins', 'Tools'}


def digest(path):
    if not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            hasher.update(block)
    return hasher.hexdigest()


def safe_path(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts or not relative.parts or relative.parts[0] not in SCOPES:
        raise ValueError('Invalid runtime deployment path')
    target = root / relative
    if not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('Runtime deployment cannot follow a link outside its root')
    return target


def synchronize(source, runtime, tracked_files, *, editors_running, revision):
    if editors_running:
        raise RuntimeError('Save and close the runtime editor before synchronizing project files')
    source, runtime = Path(source), Path(runtime)
    manifest = runtime / 'Saved/CoPresenceDeployment/files.json'
    previous = json.loads(manifest.read_text())['files'] if manifest.exists() else {}
    files = sorted({p for p in tracked_files if Path(p).parts and Path(p).parts[0] in SCOPES})
    desired = {}
    for name in files:
        src = safe_path(source, name)
        value = digest(src)
        if value is None:
            raise RuntimeError('Tracked source file is missing: ' + name)
        with src.open('rb') as stream:
            if stream.read(80).startswith(b'version https://git-lfs.github.com/spec/v1'):
                raise RuntimeError('Fetch Git LFS content before deploying: ' + name)
        desired[name] = value
    # Complete conflict check before the first write. Existing identical files
    # can seed a manifest; differing untracked runtime content needs reconciliation.
    for name in sorted(set(previous) | set(desired)):
        current = digest(safe_path(runtime, name))
        if current != desired.get(name) and current != previous.get(name) and (current is not None or name in previous):
            raise RuntimeError('Runtime has a local edit; preserve/reconcile it first: ' + name)
    changed = []
    for name, value in desired.items():
        target = safe_path(runtime, name)
        if digest(target) == value:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + '.copresence-sync-tmp')
        shutil.copy2(safe_path(source, name), temporary)
        if digest(temporary) != value:
            temporary.unlink()
            raise RuntimeError('Source changed during deployment: ' + name)
        os.replace(temporary, target)
        changed.append(name)
    for name in previous.keys() - desired.keys():
        target = safe_path(runtime, name)
        if target.exists():
            target.unlink()
            changed.append(name)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = manifest.with_suffix('.tmp')
    temporary.write_text(json.dumps({'revision': revision, 'files': desired}, indent=2))
    os.replace(temporary, manifest)
    return {'revision': revision, 'changed': changed, 'verified_files': len(desired)}
