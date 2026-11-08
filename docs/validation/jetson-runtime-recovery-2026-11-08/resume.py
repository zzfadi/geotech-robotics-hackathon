#!/usr/bin/env python3
"""Prepare the pinned arm runtime and run only reviewed offline checks.

Preparation helper only. Requires a real non-root Jetson login and --apply.
No sudo, CAN setup, USB scan, motor commands, or controller startup is included.
"""
import argparse
import ctypes.util
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import pwd
import re
import shutil
import subprocess
import sys
import tarfile

RUNTIME_NAME = 'jetson-runtime-ready.tar.gz'
RUNTIME_SHA256 = 'ab7c3c02e743e07442394dd916dd9fb3605ebc22809ffe6797a6cea53ee59c85'
BUNDLE_NAME = 'geotech-robotics-hackathon-119d38b.bundle'
BUNDLE_SHA256 = '0f04f5c09baddd5089bb41c35aca78f929f088529a1da7bf425cd4d720503db4'
BUNDLE_COMMIT = '119d38be9a91feae23fdfc1d4d39ede8fd7a697a'
REVIEWED_TOOLS_TREE = 'd2d77d0685048494972cca98673a8dce23f1e204'
REVIEWED_REQUIREMENTS_BLOB = '3cb6c49eda024baf6f2418d56f07d39f2843df4a'
ORIGIN = 'https://github.com/zzfadi/geotech-robotics-hackathon.git'
UV_ARCHIVE = 'uv-aarch64-unknown-linux-gnu-0.13.7.tar.gz'
PYTHON_ARCHIVE = 'cpython-3.12.15+20261021-aarch64-unknown-linux-gnu-install_only_stripped.tar.gz'
SDK_ARCHIVE = 'maker-arm-sdk-0245316c05cb3e8d900f35e70c08e2e18e36731e.tar.gz'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def command(args, cwd=None, env=None, capture=False):
    result = subprocess.run([str(x) for x in args], cwd=cwd, env=env,
                            text=True, capture_output=capture, check=True)
    return result.stdout.strip() if capture else ''


def git(args, cwd=None, env=None):
    return command(['git', '-c', 'core.hooksPath=/dev/null', *args],
                   cwd=cwd, env=env, capture=True)


def safe_members(archive, destination, root, allow_links):
    members = archive.getmembers()
    require(len(members) <= 12000, 'Archive has unexpectedly many members.')
    require(sum(m.size for m in members if m.isfile()) < 2 * 1024**3,
            'Archive expands beyond the preparation bound.')
    seen = set()
    for member in members:
        path = PurePosixPath(member.name)
        require(not path.is_absolute() and '..' not in path.parts and path.parts
                and path.parts[0] == root, 'Unsafe archive path: ' + member.name)
        require(member.name not in seen, 'Duplicate archive path: ' + member.name)
        seen.add(member.name)
        require(member.isfile() or member.isdir() or
                (allow_links and (member.issym() or member.islnk())),
                'Unsupported archive member: ' + member.name)
        tarfile.data_filter(member, str(destination))
    return members


def extract(archive_path, destination, root, allow_links=False):
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = safe_members(archive, destination, root, allow_links)
        archive.extractall(destination, members=members, filter='data')


def verify_runtime_files(runtime):
    entries = []
    for line in (runtime / 'SHA256SUMS').read_text().splitlines():
        expected, name = line.split('  ', 1)
        path = PurePosixPath(name)
        require(re.fullmatch(r'[0-9a-f]{64}', expected) and not path.is_absolute()
                and '..' not in path.parts, 'Invalid runtime checksum record.')
        require((runtime / name).is_file() and digest(runtime / name) == expected,
                'Runtime artifact checksum mismatch: ' + name)
        entries.append(name)
    require(len(entries) == 20 and len(set(entries)) == 20,
            'Unexpected runtime checksum inventory.')
    # The SDK is installed from source by uv; validate its member paths first.
    with tarfile.open(runtime / SDK_ARCHIVE, 'r:gz') as archive:
        safe_members(archive, runtime / 'sdk-inspection-only', SDK_ARCHIVE[:-7], False)


def inspect_shared_libraries(paths):
    missing = []
    for path in paths:
        result = subprocess.run(['ldd', str(path)], text=True, capture_output=True)
        details = (result.stdout + result.stderr).splitlines()
        failures = [line.strip() for line in details if 'not found' in line]
        if failures or result.returncode:
            missing.extend([str(path) + ': ' + line for line in failures or details])
    require(not missing, 'Missing interpreter/uv libraries; no system packages installed:\n' + '\n'.join(missing))


import email
import io
import tomllib
import zipfile

BUILD_PINS = [('setuptools', '84.2.1'), ('wheel', '0.48.0'), ('packaging', '26.3')]


def verify_existing_cache(archive_path, runtime):
    require(digest(archive_path) == RUNTIME_SHA256, 'Original runtime tar SHA-256 mismatch.')
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive.getmembers():
            # These trusted text files contain the requirements and artifact hashes.
            if member.isfile() and member.size < 100000 and not member.name.endswith('.whl'):
                relative = PurePosixPath(member.name).relative_to('jetson-runtime')
                actual = runtime / str(relative)
                require(actual.is_file() and actual.read_bytes() == archive.extractfile(member).read(),
                        'Runtime metadata changed: ' + str(relative))
    verify_runtime_files(runtime)
    for archive_name, root, links in [(UV_ARCHIVE, 'uv-aarch64-unknown-linux-gnu', False),
                                      (PYTHON_ARCHIVE, 'python', True)]:
        with tarfile.open(runtime / archive_name, 'r:gz') as archive:
            members = safe_members(archive, runtime / 'unpack', root, links)
            for member in members:
                path = runtime / 'unpack' / member.name
                if member.issym():
                    require(path.is_symlink() and os.readlink(path) == member.linkname,
                            'Unpacked runtime symlink changed: ' + member.name)
                elif member.isfile():
                    # Imports may regenerate CPython caches; retain type/link checks.
                    if root == 'python' and '__pycache__' in PurePosixPath(member.name).parts and member.name.endswith('.pyc'):
                        require(path.is_file() and not path.is_symlink(),
                                'Unexpected bytecode cache type: ' + member.name)
                        continue
                    expected = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
                    require(path.is_file() and not path.is_symlink() and digest(path) == expected,
                            'Unpacked runtime file changed: ' + member.name)
                else:
                    require(member.isdir() and path.is_dir(), 'Unexpected unpacked archive member.')


def verify_dependency_closure(runtime, manifest):
    # Import packaging directly from the already hash-verified pure-Python wheel;
    # this does not install it or use any account/system Python environment.
    packaging_entry = next(e for e in manifest['artifacts'] if e.get('package') == 'packaging')
    sys.path.insert(0, str(runtime / packaging_entry['path']))
    from packaging.requirements import Requirement
    from packaging.specifiers import SpecifierSet
    from packaging.utils import canonicalize_name
    packages = {}
    for entry in manifest['artifacts']:
        if 'package' not in entry:
            continue
        with zipfile.ZipFile(runtime / entry['path']) as archive:
            metadata_path = next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))
            metadata = email.message_from_bytes(archive.read(metadata_path))
        packages[canonicalize_name(metadata['Name'])] = {
            'version': metadata['Version'], 'python': metadata['Requires-Python'],
            'requires': metadata.get_all('Requires-Dist', [])}
    with tarfile.open(runtime / SDK_ARCHIVE, 'r:gz') as archive:
        member = next(m for m in archive.getmembers() if m.name.endswith('/pyproject.toml'))
        project = tomllib.loads(archive.extractfile(member).read().decode())
    sdk = project['project']
    packages['maker-arm'] = {'version': sdk['version'], 'python': sdk['requires-python'],
                            'requires': sdk.get('dependencies', []) +
                            sdk.get('optional-dependencies', {}).get('dev', [])}
    environment = {'implementation_name': 'cpython', 'implementation_version': '3.12.15',
                   'os_name': 'posix', 'platform_machine': 'aarch64',
                   'platform_python_implementation': 'CPython', 'platform_system': 'Linux',
                   'platform_release': platform.release(), 'platform_version': platform.version(),
                   'python_full_version': '3.12.15', 'python_version': '3.12', 'sys_platform': 'linux', 'extra': ''}
    active = {}
    for name, metadata in packages.items():
        require(not metadata['python'] or SpecifierSet(metadata['python']).contains('3.12.15'),
                'Python version incompatible with ' + name)
        active[name] = []
        for text in metadata['requires']:
            dependency = Requirement(text)
            if dependency.marker and not dependency.marker.evaluate(environment):
                continue
            canonical = canonicalize_name(dependency.name)
            require(canonical in packages and dependency.specifier.contains(packages[canonical]['version']),
                    'Cached dependency closure incomplete: ' + name + ' requires ' + str(dependency))
            active[name].append(str(dependency))
    for text in project['build-system']['requires']:
        dependency = Requirement(text)
        name = canonicalize_name(dependency.name)
        require(name in dict(BUILD_PINS) and dependency.specifier.contains(dict(BUILD_PINS)[name]),
                'SDK build-system requirement not covered: ' + text)
    require(active['wheel'] == ['packaging>=24.0'] and not active['setuptools'] and not active['packaging'],
            'Build dependency closure changed from reviewed metadata.')
    return active


def main():
    parser = argparse.ArgumentParser(description='Resume only the reviewed offline dependency installation and tests; preserve existing directories.')
    parser.add_argument('--assets', type=Path, required=True)
    parser.add_argument('--expected-user', default='fadi')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    require(args.apply, 'Explicit --apply is required after review.')
    require(os.getuid() != 0 and pwd.getpwuid(os.getuid()).pw_name == args.expected_user,
            'Run as the expected non-root Jetson user.')
    require(sys.version_info >= (3, 12) and platform.system() == 'Linux' and platform.machine() == 'aarch64',
            'Requires the installed ARM64 Jetson with system Python3.12.')
    release = platform.freedesktop_os_release()
    require(release.get('ID') == 'ubuntu' and release.get('VERSION_ID') == '24.04', 'Unexpected OS.')
    require('autoinstall' not in Path('/proc/cmdline').read_text(), 'Do not run in the live installer.')
    home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    runtime, repo = home / 'jetson-runtime', home / 'geotech-robotics-hackathon'
    for path in (runtime, repo, runtime / '.venv'):
        require(path.is_dir() and not path.is_symlink() and path.stat().st_uid == os.getuid(),
                'Existing user-owned directory required: ' + str(path))
    env = os.environ.copy()
    for key in ('PYTHONPATH', 'PYTHONHOME'):
        env.pop(key, None)
    env.update({'GIT_TERMINAL_PROMPT': '0', 'GIT_LFS_SKIP_SMUDGE': '1', 'UV_OFFLINE': '1',
                'UV_NO_CONFIG': '1', 'UV_PYTHON_DOWNLOADS': 'never', 'PYTHONNOUSERSITE': '1',
                'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'UV_CACHE_DIR': str(runtime / '.uv-cache')})
    require(git(['remote', 'get-url', 'origin'], cwd=repo, env=env) == ORIGIN, 'Unexpected repository origin.')
    require(git(['branch', '--show-current'], cwd=repo, env=env) == 'main', 'Repository is not on main.')
    require(not git(['status', '--porcelain'], cwd=repo, env=env), 'Preserve existing checkout changes; review before resuming.')
    git(['merge-base', '--is-ancestor', BUNDLE_COMMIT, 'HEAD'], cwd=repo, env=env)
    require(git(['rev-parse', 'HEAD:arm/tools'], cwd=repo, env=env) == REVIEWED_TOOLS_TREE and
            git(['rev-parse', 'HEAD:arm/requirements.txt'], cwd=repo, env=env) == REVIEWED_REQUIREMENTS_BLOB,
            'Arm code or dependencies changed since review.')
    head = git(['rev-parse', 'HEAD'], cwd=repo, env=env)
    verify_existing_cache(args.assets.expanduser().resolve() / RUNTIME_NAME, runtime)
    manifest = json.loads((runtime / 'MANIFEST.json').read_text())
    active = verify_dependency_closure(runtime, manifest)
    uv = runtime / 'unpack/uv-aarch64-unknown-linux-gnu/uv'
    python = runtime / '.venv/bin/python'
    base_python = runtime / 'unpack/python/bin/python3.12'
    require(python.resolve() == base_python.resolve(), '.venv interpreter does not point to the verified cached Python.')
    inspect_shared_libraries([uv, base_python])
    info = json.loads(command([python, '-c', 'import json,sys; print(json.dumps({"version":sys.version.split()[0],"prefix":sys.prefix,"base":sys.base_prefix}))'], env=env, capture=True))
    require(info['version'] == '3.12.15' and Path(info['prefix']).resolve() == (runtime / '.venv').resolve()
            and Path(info['base']).resolve() == (runtime / 'unpack/python').resolve(), 'Existing virtual environment identity mismatch.')
    build_lines = []
    for name, version in BUILD_PINS:
        entry = next(e for e in manifest['artifacts'] if e.get('package') == name)
        require(entry['version'] == version, 'Pinned build package version changed.')
        build_lines.append(f"{name}=={version} --hash=sha256:{entry['sha256']}")
    corrected = '\n'.join(build_lines) + '\n'
    old = '\n'.join(build_lines[:2]) + '\n'
    lock = runtime / 'build-requirements.hashed.txt'
    require(not lock.is_symlink() and (not lock.exists() or lock.read_text() in (old, corrected)),
            'Build lock has unrelated changes; inspect before replacing.')
    os.umask(0o077)
    temporary = runtime / 'build-requirements.hashed.txt.resume-tmp'
    require(not temporary.exists(), 'A previous temporary lock remains; inspect before retrying.')
    temporary.write_text(corrected)
    temporary.replace(lock)
    (runtime / 'DEPENDENCY-CLOSURE-VERIFIED.json').write_text(json.dumps(active, indent=2) + '\n')
    print('Existing cache, environment, checkout, and complete dependency closure verified.', flush=True)
    command([uv, 'pip', 'install', '--python', python, '--no-index', '--find-links', 'wheels',
             '--require-hashes', '-r', lock], cwd=runtime, env=env)
    command([uv, 'pip', 'install', '--python', python, '--no-index', '--find-links', 'wheels',
             '--no-build-isolation', '--require-hashes', '-r', 'offline-requirements.txt'], cwd=runtime, env=env)
    command([uv, 'pip', 'check', '--python', python], cwd=runtime, env=env)
    missing = [] if ctypes.util.find_library('usb-1.0') else ['libusb-1.0 (Ubuntu package libusb-1.0-0)']
    print('Missing system libraries:', missing or 'none detected', flush=True)
    env['ARMCTL_PYTHON'] = str(python)
    command(['bash', 'arm/tools/arm', '--help'], cwd=repo, env=env)
    command(['bash', 'arm/tools/arm', 'test', '--noconftest', '-c', '/dev/null'], cwd=repo, env=env)
    command([python, 'arm/tools/pickup_client.py', '--help'], cwd=repo, env=env)
    command([python, 'arm/tools/pickup_server.py', '--help'], cwd=repo, env=env)
    report = {'scope': 'Offline runtime resumed after build hash closure correction', 'git_head': head,
              'origin': ORIGIN, 'runtime_sha256': RUNTIME_SHA256, 'python': str(python),
              'build_dependencies': dict(BUILD_PINS), 'missing_system_libraries': missing,
              'checks': ['complete metadata dependency closure', 'uv pip check', 'arm --help',
                         'arm test', 'pickup_client --help', 'pickup_server --help'],
              'controller_started': False, 'hardware_access_tested': False}
    (runtime / 'PROVISIONING-RESULT.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Offline runtime and reviewed checks complete:', runtime / 'PROVISIONING-RESULT.json')


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('STOP:', str(error), file=sys.stderr)
        sys.exit(1)
