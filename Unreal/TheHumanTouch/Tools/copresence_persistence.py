"""External physical-file gate after a successful native human-host save.

Run from ordinary host Python/SSH, NEVER editor Python (Concert virtualizes its
file reads). A clean editor or an autosave is not durable persistence evidence.
This tool does not save, alter, unlock, overwrite, or recover Unreal packages.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re


class PersistenceError(RuntimeError):
    pass


def verify_physical_packages(content, manifest):
    """Verify every explicit native-save hash; reject ambiguous/unsafe manifests."""
    content = Path(content).resolve(strict=True)
    entries = manifest.get('files', [])
    if not entries:
        raise PersistenceError('Native-save manifest has no files')
    results, seen = [], set()
    for entry in entries:
        package = entry.get('package', '')
        if not re.fullmatch(r'/Game/[A-Za-z0-9_\-]+(?:/[A-Za-z0-9_\-]+)*', package):
            raise PersistenceError('Invalid project package path')
        if package.casefold() in seen:
            raise PersistenceError('Duplicate native-save package: '+package)
        seen.add(package.casefold())
        digest, size = entry.get('sha256'), entry.get('bytes')
        if not isinstance(digest, str) or not re.fullmatch('[a-fA-F0-9]{64}', digest):
            raise PersistenceError('Missing native-save SHA-256: '+package)
        if type(size) is not int or size <= 0:
            raise PersistenceError('Invalid native-save byte count: '+package)
        extension = entry.get('extension', '.umap' if package.startswith('/Game/Maps/') else '.uasset')
        if extension not in ('.umap', '.uasset'):
            raise PersistenceError('Invalid package extension: '+package)
        path = content / (package[len('/Game/'):]+extension)
        try:
            resolved = path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise PersistenceError('Native-saved package missing from physical Content: '+package) from exc
        if not resolved.is_relative_to(content) or not resolved.is_file():
            raise PersistenceError('Package escapes physical Content: '+package)
        # Stream large packages instead of reading a full Fab pack into memory.
        before = resolved.stat()
        h = hashlib.sha256()
        with resolved.open('rb') as stream:
            for block in iter(lambda: stream.read(1024*1024), b''):
                h.update(block)
        after = resolved.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
            raise PersistenceError('Physical package changed during verification: '+package)
        if after.st_size != size or h.hexdigest() != digest.lower():
            raise PersistenceError('Physical Content differs from native save; preserve both: '+package)
        results.append({'package': package, 'bytes': size, 'sha256': h.hexdigest()})
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--content', type=Path, required=True)
    p.add_argument('--manifest', type=Path, required=True)
    args = p.parse_args()
    try:
        result = verify_physical_packages(args.content, json.loads(args.manifest.read_text()))
    except (OSError, ValueError, PersistenceError) as exc:
        p.exit(1, 'PERSISTENCE BLOCKED: '+str(exc)+'\n')
    print(json.dumps({'state': 'physical_verified', 'count': len(result), 'files': result}))


if __name__ == '__main__':
    main()
