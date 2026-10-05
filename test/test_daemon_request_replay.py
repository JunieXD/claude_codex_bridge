import json
import socket
from threading import Thread
from types import SimpleNamespace

import pytest

from ccbd.api_models import DeliveryScope, MessageEnvelope, RpcResponse
from ccbd.services.dispatcher import JobDispatcher
from ccbd.services.registry import AgentRegistry
from ccbd.socket_client import CcbdClient, CcbdClientError
from cli.services import daemon
from storage.paths import PathLayout
from test_v2_ccbd_dispatcher import _bootstrap_test_project, _provider_config, _runtime


def invoke_fixture(monkeypatch, tmp_path, operation, *, failure='drop'):
    requests = []
    connections = []

    class FixtureSocket:
        def sendall(self, payload):
            requests.append(json.loads(payload))

        def recv(self, size):
            if len(requests) == 1 and failure != 'connect':
                if failure == 'invalid':
                    return b'not-json\n'
                if failure == 'server':
                    return (json.dumps(RpcResponse.failure('rejected').to_record()) + '\n').encode()
                return b''
            return (json.dumps(RpcResponse.success({'job_id':'receipt'}).to_record()) + '\n').encode()

        def close(self):
            pass

    def connect_socket(*arguments, **keywords):
        connections.append(1)
        if failure == 'connect' and len(connections) == 1:
            raise ConnectionRefusedError('connection refused')
        return FixtureSocket()

    monkeypatch.setattr('ccbd.socket_client.connect_socket', connect_socket)
    client = CcbdClient(tmp_path / 'fixture.sock')
    context = SimpleNamespace(project=SimpleNamespace(source='test'))
    monkeypatch.setattr(daemon, 'connect_mounted_daemon', lambda *arguments, **keywords: SimpleNamespace(client=client))
    monkeypatch.setattr(daemon, 'inspect_daemon', lambda context: (
        None, None, SimpleNamespace(phase='mounted', desired_state='running'),
    ))
    return requests, connections, lambda: daemon.invoke_mounted_daemon(
        context, allow_restart_stale=True, request_fn=lambda connection: connection.request(operation, {}),
    )


@pytest.mark.parametrize('operation', ['submit', 'retry', 'resubmit', 'followup', 'ack', 'start', 'cancel'])
@pytest.mark.parametrize('failure', ['drop', 'invalid'])
def test_uncertain_mutation_is_not_replayed(monkeypatch, tmp_path, operation, failure):
    requests, connections, invoke = invoke_fixture(monkeypatch, tmp_path, operation, failure=failure)
    with pytest.raises(CcbdClientError, match='result is unknown') as captured:
        invoke()
    assert 'ccb queue all' in str(captured.value)
    assert len(requests) == len(connections) == 1


def test_observer_is_replayed_after_lost_response(monkeypatch, tmp_path):
    requests, _connections, invoke = invoke_fixture(monkeypatch, tmp_path, 'queue')
    assert invoke()['job_id'] == 'receipt'
    assert len(requests) == 2


def test_mutation_can_retry_when_connection_failed_before_send(monkeypatch, tmp_path):
    requests, connections, invoke = invoke_fixture(monkeypatch, tmp_path, 'submit', failure='connect')
    assert invoke()['job_id'] == 'receipt'
    assert len(requests) == 1
    assert len(connections) == 2


def test_semantic_server_error_is_not_replayed(monkeypatch, tmp_path):
    requests, _connections, invoke = invoke_fixture(monkeypatch, tmp_path, 'submit', failure='server')
    with pytest.raises(CcbdClientError, match='rejected'):
        invoke()
    assert len(requests) == 1


@pytest.mark.skipif(not hasattr(socket, 'AF_UNIX'), reason='Unix sockets are required')
def test_real_lost_reply_keeps_exactly_one_accepted_job(monkeypatch, tmp_path):
    project = _bootstrap_test_project(tmp_path / 'repo')
    layout = PathLayout(project.project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=project.project_id, layout=layout, pid=101))
    dispatcher = JobDispatcher(layout, config, registry)
    endpoint = tmp_path / 'audit.sock'
    accepted = []
    errors = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(endpoint))
        server.listen(1)
        server.settimeout(3)

        def accept_and_drop_reply():
            try:
                connection, _peer = server.accept()
                with connection:
                    connection.settimeout(3)
                    frame = b''
                    while b'\n' not in frame:
                        chunk = connection.recv(65536)
                        if not chunk:
                            raise RuntimeError('missing request')
                        frame += chunk
                    payload = json.loads(frame)['request']
                    payload['delivery_scope'] = DeliveryScope(payload['delivery_scope'])
                    accepted.append(dispatcher.submit(MessageEnvelope(**payload)).jobs[0].job_id)
            except Exception as error:
                errors.append(error)

        worker = Thread(target=accept_and_drop_reply, daemon=True)
        worker.start()
        client = CcbdClient(endpoint)
        monkeypatch.setattr(daemon, 'connect_mounted_daemon', lambda *arguments, **keywords: SimpleNamespace(client=client))
        monkeypatch.setattr(daemon, 'inspect_daemon', lambda context: (
            None, None, SimpleNamespace(phase='mounted', desired_state='running'),
        ))
        request = MessageEnvelope(
            project_id=project.project_id, to_agent='codex', from_actor='user', body='fixture',
            task_id=None, reply_to=None, message_type='ask', delivery_scope=DeliveryScope.SINGLE,
        )
        try:
            with pytest.raises(CcbdClientError, match='result is unknown'):
                daemon.invoke_mounted_daemon(
                    SimpleNamespace(project=project), allow_restart_stale=True,
                    request_fn=lambda connection: connection.submit(request),
                )
        finally:
            worker.join(timeout=5)
        assert not worker.is_alive() and not errors
    assert len(accepted) == 1
    assert registry.get('codex').queue_depth == 1
