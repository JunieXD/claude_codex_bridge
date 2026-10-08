"""Real Windows host-input regression; only operates on a marked E2E project.

Compare --wezterm paths to test old/new ConPTY with exactly the same workload.
No global keyboard injection, clipboard mutation, or model submissions.
"""
import argparse
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from e2e_local import ROOT, Runner, environment


def prompt(text):
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(('❯', '›'))]
    if not starts:
        raise RuntimeError('No idle provider prompt found')
    result = []
    for line in lines[starts[-1]:]:
        if result and (not line.strip() or line.startswith('─')):
            break
        result.append(line.strip())
    return '\n'.join(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('project', 'output', 'wezterm', 'herdr', 'sh'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != 'win32' or not (args.project / '.ccb-native-e2e').is_file():
        parser.error('Requires native Windows and a marked disposable E2E project')
    runner = Runner(args.project.resolve(), args.output.resolve(), environment(args.herdr, args.sh))
    config = runner.output / 'input-e2e.lua'
    command = ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
               str(ROOT / 'platforms/windows/start.ps1'), '-ProjectRoot', str(runner.project), '-NoPause']
    config.write_text('return {default_cwd=' + json.dumps(str(runner.project), ensure_ascii=False) +
                      ', default_prog={' + ','.join(json.dumps(s, ensure_ascii=False) for s in command) +
                      '}, initial_cols=160, initial_rows=42, exit_behavior="Hold"}', encoding='utf-8')
    env = dict(runner.env, CCB_PYTHON=sys.executable)
    gui = subprocess.Popen([str(args.wezterm.with_name('wezterm-gui.exe')), '--config-file', str(config),
                            'start', '--always-new-process'], env=env, creationflags=subprocess.CREATE_NO_WINDOW)
    runner.wezterm = args.wezterm
    runner.gui_env = dict(env, WEZTERM_UNIX_SOCKET=str(Path.home() / '.local/share/wezterm' / f'gui-sock-{gui.pid}'))
    first_result = len(runner.results)
    try:
        identities = runner.identity()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            visible = runner.wez('get-text', '--pane-id', '0').stdout
            if 'Claude' in visible and 'Codex' in visible:
                break
            time.sleep(1)
        else:
            raise RuntimeError('Foreground CCB UI did not attach')
        time.sleep(3)
        user32 = ctypes.windll.user32
        handles = []
        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def enum(hwnd, _):
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(ctypes.c_void_p(hwnd), ctypes.byref(pid))
            if pid.value == gui.pid and user32.IsWindowVisible(ctypes.c_void_p(hwnd)):
                handles.append(hwnd)
            return True
        user32.EnumWindows(enum, 0)
        if len(handles) != 1:
            raise RuntimeError(f'Expected one owned GUI window, got {len(handles)}')
        hwnd = ctypes.c_void_p(handles[0])
        class Rect(ctypes.Structure):
            _fields_ = [(s, ctypes.c_long) for s in ('left', 'top', 'right', 'bottom')]
        rect = Rect()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        width, height = rect.right-rect.left, rect.bottom-rect.top
        panes = [p for p in runner.panes() if (p.get('tokens') or {}).get('ccb_role') == 'agent']
        for provider in ('claude', 'codex'):
            pane = next(p for p in panes if p.get('display_agent') == provider)
            pane_id = pane['pane_id']
            current = next(p for p in runner.panes() if p.get('focused'))
            if current['pane_id'] != pane_id:
                runner.herdr('pane', 'focus', '--pane', current['pane_id'], '--direction',
                             'left' if provider == 'codex' else 'right').check_returncode()
            time.sleep(1)
            assert next(p for p in runner.panes() if p.get('focused'))['pane_id'] == pane_id
            def snap(name):
                content = runner.herdr('pane', 'read', pane_id, '--lines', '20', '--format', 'text').stdout
                (runner.output / f'{provider}-{name}.txt').write_text(content, encoding='utf-8')
                return prompt(content)
            baseline = snap('initial')
            if baseline not in ('❯', '› Ask Codex to do anything'):
                raise RuntimeError(f'Test prompt must initially be empty: {baseline!r}')
            def unchanged(name):
                actual = snap(name)
                runner.record(f'{provider}_{name}', actual == baseline, expected=baseline, actual=actual)
                if actual != baseline:
                    runner.herdr('pane', 'send-keys', pane_id, 'ctrl+c')
                    time.sleep(2)
            time.sleep(30)
            unchanged('idle_30s')
            for name, payload in (
                ('legacy_mouse_reports', ''.join('\x1b[MC' + chr(x+32) + '*' for x in range(25, 0, -1))),
                ('sgr_mouse_reports', ''.join(f'\x1b[<35;{x};10M' for x in range(25, 0, -1))),
            ):
                runner.wez('send-text', '--pane-id', '0', '--no-paste', input_text=payload).check_returncode()
                time.sleep(2)
                unchanged(name)
            for step in range(3):
                user32.PostMessageW(hwnd, 0x8, 0, 0)  # focus loss/gain on owned test window
                user32.PostMessageW(hwnd, 0x7, 0, 0)
                user32.SetWindowPos(hwnd, None, 0, 0, width-80*(step % 2), height-40*(step % 2), 0x16)
                time.sleep(.6)
                for x in range(min(width-30, 1100), 20, -5):
                    user32.PostMessageW(hwnd, 0x200, 0, (200 << 16) | x)
                    time.sleep(.004)
            user32.SetWindowPos(hwnd, None, 0, 0, width, height, 0x16)
            time.sleep(2)
            unchanged('focus_resize_mouse')
            for name, value, raw in [('unicode_paste', '中文_输入123!', False),
                                     ('ascii_punctuation', 'CCB_9876!@#', True)]:
                runner.wez('send-text', '--pane-id', '0', *(['--no-paste'] if raw else []), input_text=value).check_returncode()
                time.sleep(1)
                actual = snap(name)
                runner.record(f'{provider}_{name}', value in actual, actual=actual)
                runner.herdr('pane', 'send-keys', pane_id, 'ctrl+u').check_returncode()
                time.sleep(1)
            runner.wez('send-text', '--pane-id', '0', '--no-paste', input_text='abc\x1b[D\x7fZ').check_returncode()
            time.sleep(1)
            actual = snap('arrow_backspace')
            runner.record(f'{provider}_arrow_backspace', 'aZc' in actual, actual=actual)
            runner.herdr('pane', 'send-keys', pane_id, 'ctrl+e').check_returncode()
            runner.herdr('pane', 'send-keys', pane_id, 'ctrl+u').check_returncode()
            time.sleep(1)
            unchanged('prompt_cleared')
        runner.record('input_checks_preserve_sessions', runner.identity() == identities)
    finally:
        gui.terminate()
        gui.wait(timeout=15)
    time.sleep(8)
    reopen = runner.cli('herdr', 'open', '--no-attach', '--wait-ready')
    runner.record('input_ui_close_preserves_sessions', reopen.returncode == 0
                  and runner.identity() == identities, exit_code=reopen.returncode)
    if any(not row['passed'] for row in runner.results[first_result:]):
        raise SystemExit(1)


if __name__ == '__main__':
    main()
