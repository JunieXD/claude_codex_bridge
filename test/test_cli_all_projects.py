from io import StringIO
from pathlib import Path
import shlex
from types import SimpleNamespace

import pytest

import cli.phase2 as phase2
from cli.entrypoint_runtime import run_cli_entrypoint
from cli.management_runtime import source_update
from cli.models import ParsedKillCommand, ParsedPsCommand
from cli.parser import CliParser, CliUsageError
from cli.services.daemon import KillSummary


def test_live_project_roots_only_discovers_this_installation(tmp_path, monkeypatch):
    root = tmp_path / 'CCB installation'
    alpha = tmp_path / 'alpha project'
    beta = tmp_path / 'beta project'
    main = str(root / 'lib/ccbd/main.py')
    commands = [
        ['python', main, '--project', str(beta)],
        ['python', main, '--project', str(alpha)],
        ['python', main, '--project', str(beta)],
        ['python', str(tmp_path / 'other/lib/ccbd/main.py'), '--project', str(alpha)],
        ['python', str(root / 'lib/ccbd/keeper_main.py'), '--project', str(alpha)],
        [str(root / 'bin/ccb-agent-sidebar'), '--project', str(alpha)],
        ['sh', '-c', shlex.join(['python', main, '--project', str(alpha)])],
        ['sh', '-lc', shlex.join(['python', main, '--project', str(alpha)])],
        ['sh', '-xc', shlex.join(['python', main, '--project', str(alpha)])],
    ]
    table = '\n'.join(f'{1000+i} {shlex.join(arguments)}' for i, arguments in enumerate(commands))
    table += '\nPID COMMAND\n\n'

    def fake_ps(arguments, **kwargs):
        assert arguments == ['ps', '-ww', '-axo', 'pid=,args=']
        assert kwargs['check']
        return SimpleNamespace(stdout=table)

    monkeypatch.setattr(source_update.subprocess, 'run', fake_ps)

    assert source_update.live_project_roots(root) == [alpha, beta]


@pytest.mark.parametrize('argv,expected', [
    (['ps', '--all'], ParsedPsCommand(project=None, all_projects=True)),
    (['kill', '--all'], ParsedKillCommand(project=None, all_projects=True)),
    (['kill', '-f', '--all'], ParsedKillCommand(project=None, force=True, all_projects=True)),
    (['kill', '--all', '-f'], ParsedKillCommand(project=None, force=True, all_projects=True)),
])
def test_parse_all_projects(argv, expected):
    assert CliParser().parse(argv) == expected


@pytest.mark.parametrize('argv', [['ps', '--all'], ['kill', '--all'], ['kill', '-f', '--all']])
def test_all_projects_rejects_explicit_project(argv):
    with pytest.raises(CliUsageError, match='--all cannot be combined with --project'):
        CliParser().parse(['--project', '/synthetic/project', *argv])


@pytest.fixture
def projects(tmp_path, monkeypatch):
    roots = [tmp_path / 'alpha project', tmp_path / 'beta project']
    summaries = {}
    for root in roots:
        (root / '.ccb').mkdir(parents=True)
        (root / '.ccb/ccb.config').write_text('demo:codex\n')
        summaries[root] = {
            'ccbd_state': 'mounted',
            'agents': [{
                'agent_name': 'demo', 'provider': 'codex', 'state': 'idle', 'queue_depth': 0,
                'binding_status': 'bound', 'runtime_ref': None, 'session_ref': None,
                'workspace_path': str(root),
            }],
        }
    cwd = tmp_path / 'outside projects'
    cwd.mkdir()
    killed = []
    observed = []

    def discover(root):
        assert root == Path(phase2.__file__).resolve().parents[2]
        return roots

    def fake_summary(context, command):
        assert command.kind == 'ps' and not command.all_projects
        assert context.project.project_root == Path(command.project)
        observed.append(context.project.project_root)
        return {'project_id': context.project.project_id, **summaries[context.project.project_root]}

    def fake_kill(context, command):
        assert command == context.command and not command.all_projects
        killed.append((context.project.project_root, command.force))
        return KillSummary(
            project_id=context.project.project_id, state='stopped',
            socket_path=str(context.paths.ccbd_socket_path), forced=command.force,
        )

    def run(argv):
        out, err = StringIO(), StringIO()
        code = phase2.maybe_handle_phase2(argv, cwd=cwd, stdout=out, stderr=err)
        return code, out.getvalue(), err.getvalue()

    monkeypatch.delenv('CCB_CALLER_PROJECT_ROOT', raising=False)
    monkeypatch.setattr(phase2, 'live_project_roots', discover)
    monkeypatch.setattr(phase2, 'ps_summary', fake_summary)
    monkeypatch.setattr(phase2, 'kill_project', fake_kill)
    return SimpleNamespace(roots=roots, summaries=summaries, killed=killed, observed=observed, run=run)


def test_kill_all_dispatches_idle_projects(projects):
    code, out, err = projects.run(['kill', '--all'])
    assert (code, err) == (0, '')
    assert projects.killed == [(root, False) for root in projects.roots]
    assert projects.observed == projects.roots
    assert out.count('kill_status: ok') == 2
    assert all(f'== {root}\n' in out for root in projects.roots)
    assert out.endswith('killed: 2, skipped: 0\n')


@pytest.mark.parametrize('state,queue_depth', [('busy', 0), ('starting', 0), ('idle', 3)])
def test_kill_all_skips_active_or_queued_projects(projects, state, queue_depth):
    alpha, beta = projects.roots
    active = {**projects.summaries[alpha]['agents'][0], 'agent_name': 'worker',
              'state': state, 'queue_depth': queue_depth}
    projects.summaries[alpha]['agents'].append(active)

    code, out, err = projects.run(['kill', '--all'])

    assert (code, err) == (1, '')
    assert projects.killed == [(beta, False)]
    assert f'skipped: busy (worker={state}, queue={queue_depth})' in out
    assert out.endswith('killed: 1, skipped: 1\n')


def test_kill_all_force_stops_busy_and_queued_projects(projects):
    agent = projects.summaries[projects.roots[0]]['agents'][0]
    agent.update(state='busy', queue_depth=3)

    code, out, err = projects.run(['kill', '-f', '--all'])

    assert (code, err) == (0, '')
    assert projects.killed == [(root, True) for root in projects.roots]
    assert out.endswith('killed: 2, skipped: 0\n')


@pytest.mark.parametrize('force', [False, True])
def test_kill_all_always_skips_callers_project(projects, monkeypatch, force):
    alpha, beta = projects.roots
    monkeypatch.setenv('CCB_CALLER_PROJECT_ROOT', str(alpha / '.'))

    code, out, err = projects.run(['kill', '--all', *(['-f'] if force else [])])

    assert (code, err) == (1, '')
    assert projects.killed == [(beta, force)]
    assert projects.observed == [beta]
    assert "skipped: current agent's project; run ccb kill there from a terminal" in out
    assert out.endswith('killed: 1, skipped: 1\n')


def test_ps_all_shows_busy_queued_and_current_projects(projects, monkeypatch):
    alpha, beta = projects.roots
    projects.summaries[alpha]['agents'][0].update(state='busy', queue_depth=2)
    monkeypatch.setenv('CCB_CALLER_PROJECT_ROOT', str(alpha))

    code, out, err = projects.run(['ps', '--all'])

    assert (code, err) == (0, '')
    assert f'== {alpha} (current)\n' in out
    assert f'== {beta}\n' in out
    assert 'state=busy provider=codex queue=2' in out
    assert projects.observed == projects.roots
    assert projects.killed == []


@pytest.mark.parametrize('kind', ['ps', 'kill'])
def test_all_projects_without_running_projects(projects, kind):
    projects.roots.clear()
    code, out, err = projects.run([kind, '--all'])
    assert (code, out, err) == (0, 'no running CCB projects\n', '')
    assert projects.observed == projects.killed == []


def test_ps_all_reuses_explicit_project_dispatch(projects):
    root = projects.roots[0]
    projects.roots[:] = [root]
    single_code, single_out, single_err = projects.run(['--project', str(root), 'ps'])
    all_code, all_out, all_err = projects.run(['ps', '--all'])
    assert (single_code, single_err) == (all_code, all_err) == (0, '')
    assert all_out == f'== {root}\n' + single_out


def test_kill_all_reports_project_errors_through_existing_handler(projects, monkeypatch):
    def fail(context, command):
        raise RuntimeError('synthetic stop failure')

    monkeypatch.setattr(phase2, 'kill_project', fail)
    code, out, err = projects.run(['kill', '--all'])
    assert code == 1
    assert 'command_status: failed\nerror: synthetic stop failure' in err
    assert f'== {projects.roots[0]}\n' in out
    assert f'== {projects.roots[1]}\n' not in out


@pytest.mark.parametrize('argv,expected', [
    (['--help'], ['ccb ps --all', 'ccb kill --all [-f]']),
    (['ps', '--help'], ['usage: ccb ps [--all]']),
    (['kill', '--help'], ['usage: ccb kill [-f] [--all]', "calling agent's project"]),
])
def test_all_projects_help(argv, expected, tmp_path):
    out, err = StringIO(), StringIO()
    code = run_cli_entrypoint(
        argv, version='8.7.8', script_root=tmp_path / 'installation', cwd=tmp_path,
        stdout=out, stderr=err,
    )
    assert (code, err.getvalue()) == (0, '')
    assert all(text in out.getvalue() for text in expected)
