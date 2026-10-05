from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(shutil.which('node') is None, reason='Node is needed for mod unit tests')
def test_claude_cache_mod_state_machine():
    result = subprocess.run(['node', '--test', str(Path(__file__).with_suffix('.mjs'))],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
