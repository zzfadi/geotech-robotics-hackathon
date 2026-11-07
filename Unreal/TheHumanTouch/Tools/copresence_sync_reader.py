"""Operator-only reader synchronization; never exposed to Agent Chat."""
import argparse
import json
from pathlib import Path

from check_editor_topology import commands
from copresence_git_roles import sync_reader


def project_is_open(processes, roots):
    roots = [str(Path(root).resolve()).replace('\\', '/').casefold().rstrip('/') + '/'
             for root in roots]
    return any('unrealeditor' in command.casefold() and '.uproject' in command.casefold()
               and any(root in command.replace('\\', '/').casefold() for root in roots)
               for command in processes)


def synchronize_checkout(root, runtime=None):
    root = Path(root).resolve()
    runtime = Path(runtime).resolve() if runtime else root
    running = project_is_open(commands(), [root, runtime])
    revision = sync_reader(root, editors_running=running, runtime=runtime)
    return {'role': 'reader', 'revision': revision, 'runtime': str(runtime)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkout', required=True)
    parser.add_argument('--runtime')
    args = parser.parse_args()
    print(json.dumps(synchronize_checkout(args.checkout, args.runtime)))
