#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT / 'lib'))

from cli.management_runtime.fork_runtime_install import install_fork_runtime
from cli.management_runtime.fork_runtime import runtime_lock_path


def main() -> int:
    parser = argparse.ArgumentParser(description='Install an independent runtime from the maintained CCB Fork.')
    parser.add_argument('--runtime-root', type=Path, default=Path.home() / 'Programs/CCB-runtime')
    parser.add_argument('--bin-dir', type=Path, default=Path.home() / '.local/bin')
    parser.add_argument('--python', default=sys.executable)
    parser.add_argument('--lock-held', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.lock_held:
        try:
            import fcntl

            descriptor = int(os.environ['CCB_FORK_INSTALL_LOCK_FD'])
            inherited = os.fstat(descriptor)
            expected = runtime_lock_path(args.runtime_root).stat()
            if (inherited.st_dev, inherited.st_ino) != (expected.st_dev, expected.st_ino):
                raise ValueError('Install lock does not match this runtime')
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (KeyError, OSError, ValueError) as error:
            parser.error(f'--lock-held requires the inherited Fork install lock: {error}')
    try:
        install_fork_runtime(SOURCE_ROOT, args.runtime_root, args.bin_dir,
                             python=args.python, lock_held=args.lock_held)
    except Exception as error:
        print(f'Fork runtime installation failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
