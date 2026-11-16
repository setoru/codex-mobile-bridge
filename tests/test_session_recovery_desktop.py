"""App recovery through Bridge and real creator I/O, with a synthetic owner."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bridge.app.service import Bridge
from bridge.clients.codex.ipc import IPCError
import test_session_recovery as fixtures


ROOT = Path(__file__).resolve().parents[1]


class RecoveryDesktopOwner:
    client_id = 'fixture-follower'

    def __init__(self, fixture):
        self.fixture = fixture
        self.calls, self.followed, self.states = [], [], {}
        self.fail_settings = False
        self.available = True
        self.revision = 0

    def connect(self):
        return None

    def close(self):
        return None

    def endpoint_changed(self):
        return False

    def follow(self, identifier, owner, following=True, *, host):
        if not following:
            return
        self.followed.append(identifier)
        if not self.available:
            raise IPCError('Synthetic owner unavailable')
        self.publish(identifier)

    def publish(self, identifier):
        if identifier not in self.states:
            # Desktop can reopen the persisted empty thread with its composer defaults.
            self.states[identifier] = self.fixture.history(identifier)
            self.states[identifier].update(latestModel='desktop-default', latestReasoningEffort='low')
        self.revision += 1
        self.fixture.bridge._event({'method': 'thread-stream-state-changed', 'version': 11,
            'sourceClientId': 'owner-' + identifier, 'params': {
                'hostId': self.fixture.bridge.native_host, 'conversationId': identifier,
                'change': {'type': 'snapshot', 'revision': self.revision,
                           'conversationState': copy.deepcopy(self.states[identifier])}}})

    def request(self, method, params, *, target, host, timeout):
        identifier = params['conversationId']
        if (identifier == self.fixture.source or target != 'owner-' + identifier or
                host != self.fixture.bridge.native_host):
            raise AssertionError('Recovery wrote to a different conversation or owner')
        self.calls.append((method, copy.deepcopy(params)))
        state = self.states[identifier]
        if method == 'thread-follower-update-thread-settings':
            settings = params['threadSettings']
            state['latestModel'] = settings['model']
            if 'effort' in settings:
                state['latestReasoningEffort'] = settings['effort']
            for key in ('model', 'effort', 'serviceTier', 'runtimeWorkspaceRoots'):
                if key in settings:
                    state.setdefault('latestThreadSettings', {})[key] = copy.deepcopy(settings[key])
            for key in ('approvalPolicy', 'approvalsReviewer'):
                if key in settings:
                    state['currentPermissions'][key] = settings[key]
            if 'permissions' in settings:
                state['currentPermissions']['activePermissionProfile'] = {'id': settings['permissions']}
            if self.fail_settings:
                raise IPCError('Synthetic settings response lost')
            self.publish(identifier)
            return {'result': {'applied': True}}
        if method == 'thread-follower-start-turn':
            start = params['turnStart']
            self.fixture.sent.append((identifier, state['latestModel'], state['latestReasoningEffort'],
                                      copy.deepcopy(start['request']['input'])))
            return {'result': {}}
        raise AssertionError('Unexpected native owner operation: ' + method)


class DesktopRecoveryTests(unittest.TestCase):
    recovery_body = fixtures.SessionRecoveryTests.recovery_body

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.cwd = self.home / 'worktree'
        self.cwd.mkdir()
        self.source = str(uuid.uuid4())
        self.target = str(uuid.uuid4())
        self.sent, self.opened = [], []
        self.log = self.home / 'creator-requests.json'
        self.persisted = self.home / 'created.json'
        (self.home / 'config.toml').write_text(
            'model_provider = "fixture-provider"\n[model_providers.fixture-provider]\n'
            'base_url = "https://fixture.invalid/v1"\nexperimental_bearer_token = "fixture-only"\n')
        self.bridge = Bridge(self.home, self.home / 'bridge', codex_bin=self.home / 'fixture-runtime')
        self.addCleanup(self.bridge.close)
        self.owner = RecoveryDesktopOwner(self)
        self.bridge.ipc = self.owner
        self.bridge.hosts.state = lambda: {}
        self.bridge.hosts.hosts = lambda: {}
        self.bridge.store = SimpleNamespace(get=self.metadata, history=self.history,
            list=lambda **kwargs: [], attached_parent=lambda identifier: None)
        script = self.home / 'runtime.py'
        script.write_text('''import json, sys
from pathlib import Path
identifier, output, log = sys.argv[1:4]
calls = []
for line in sys.stdin:
    request = json.loads(line)
    calls.append(request)
    if 'id' not in request:
        continue
    method, params = request['method'], request['params']
    if method == 'thread/start':
        thread = {'id': identifier, 'cwd': params['cwd'], 'modelProvider': params['modelProvider']}
        result = {'thread': thread, 'model': params['model'],
                  'reasoningEffort': params['config']['model_reasoning_effort'],
                  'activePermissionProfile': {'id': params['permissions']},
                  **{key: params[key] for key in ('serviceTier','approvalPolicy','approvalsReviewer') if key in params}}
        Path(output).write_text(json.dumps(result))
    elif method == 'thread/read':
        result = {'thread': json.loads(Path(output).read_text())['thread']}
    else:
        result = {}
    print(json.dumps({'id': request['id'], 'result': result}), flush=True)
Path(log).write_text(json.dumps(calls))
''')
        real_popen = subprocess.Popen
        self.native_popen = real_popen
        self.runtime_script = script
        def spawn(argv, **kwargs):
            self.assertEqual(argv, [str(self.home / 'fixture-runtime'), 'app-server', '--listen', 'stdio://'])
            return real_popen([sys.executable, str(script), self.target, str(self.persisted), str(self.log)], **kwargs)
        def open_desktop(argv, **kwargs):
            self.assertIn(argv[0], ('open', 'xdg-open'))
            self.opened.append(argv[1])
            if not self.owner.available:
                raise OSError('Synthetic desktop opener unavailable')
            return subprocess.CompletedProcess(argv, 0)
        for target, replacement in (('bridge.features.sessions.create.subprocess.Popen', spawn),
                                    ('bridge.features.sessions.create.subprocess.run', open_desktop)):
            patcher = patch(target, side_effect=replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        if sys.platform == 'win32':
            patcher = patch('bridge.features.sessions.create.os.startfile', side_effect=self.opened.append)
            patcher.start()
            self.addCleanup(patcher.stop)

    def metadata(self, identifier):
        if identifier != self.source and not self.persisted.exists():
            raise ValueError('Synthetic conversation is not persisted')
        return {'id': identifier, 'cwd': str(self.cwd), 'origin': 'desktop',
                'model_provider': 'fixture-provider', 'title': '原任务' if identifier == self.source else ''}

    def history(self, identifier, **kwargs):
        self.metadata(identifier)
        return {'id': identifier, 'cwd': str(self.cwd), 'title': '原任务' if identifier == self.source else '',
                'modelProvider': 'fixture-provider', 'latestModel': 'source-model',
                'latestReasoningEffort': 'high', 'latestThreadSettings': {'serviceTier': 'fast'},
                'currentPermissions': {'activePermissionProfile': {'id': ':workspace'},
                                       'approvalPolicy': 'on-request', 'approvalsReviewer': 'user'},
                'turns': [], 'requests': [], 'historyComplete': True,
                'threadRuntimeStatus': {'type': 'idle'}}

    def test_unknown_owner_settings_are_read_back_without_repeating_writes(self):
        self.owner.fail_settings = True
        operation = str(uuid.uuid4())
        result = self.bridge.recover(self.source, self.recovery_body(operation))
        self.assertEqual(result['status'], 'unknown', result)
        self.assertEqual(result['id'], self.target)
        self.assertEqual(self.sent, [])
        self.owner.fail_settings = False
        self.owner.publish(self.target)
        recovered = self.bridge.recover(self.source, request_id=operation)
        self.assertEqual(recovered['status'], 'not_submitted', recovered)
        self.assertEqual(self.sent, [])
        self.assertEqual([method for method, _ in self.owner.calls], ['thread-follower-update-thread-settings'])
        confirmed = self.bridge.recover(self.source, self.recovery_body(operation))
        self.assertEqual(confirmed['status'], 'confirmed', confirmed)
        self.assertEqual(self.sent[0][:3], (self.target, 'source-model', 'high'))
        self.assertEqual(self.sent[0][3][0]['text'],
                         '读取会话[原任务](codex://threads/' + self.source + ')，继续完成')
        self.assertNotIn(self.source, self.owner.followed)

    def test_unavailable_app_keeps_created_id_for_explicit_continuation(self):
        self.owner.available = False
        operation = str(uuid.uuid4())
        result = self.bridge.recover(self.source, self.recovery_body(operation))
        self.assertEqual(result['status'], 'not_submitted', result)
        self.assertEqual(result['id'], self.target)
        self.assertEqual(self.sent, [])
        self.owner.available = True
        result = self.bridge.recover(self.source, self.recovery_body(operation))
        self.assertEqual((result['status'], result['id']), ('confirmed', self.target), result)
        methods = [row['method'] for row in json.loads(self.log.read_text())]
        self.assertEqual(methods.count('thread/start'), 1)
        self.assertEqual(len(self.sent), 1)

    def test_completed_receipt_does_not_override_later_native_model_changes(self):
        operation = str(uuid.uuid4())
        result = self.bridge.recover(self.source, self.recovery_body(operation))
        self.assertEqual(result['status'], 'confirmed', result)
        state = self.owner.states[self.target]
        state['latestModel'] = 'later-user-model'
        state['latestThreadSettings']['model'] = 'later-user-model'
        self.owner.publish(self.target)
        previous_calls = copy.deepcopy(self.owner.calls)
        for duplicate in (self.bridge.recover(self.source, self.recovery_body(operation)),
                          self.bridge.recover(self.source, request_id=operation)):
            self.assertEqual((duplicate['status'], duplicate['id']), ('confirmed', self.target))
        self.assertEqual(self.owner.states[self.target]['latestModel'], 'later-user-model')
        self.assertEqual(self.owner.calls, previous_calls)
        self.assertEqual(len(self.sent), 1)

    def test_remote_app_creates_on_original_host_with_source_settings(self):
        self.bridge.close()
        host, alias = 'ssh:fixture-host', 'fixture-ssh'
        self.bridge = Bridge(self.home, self.home / 'remote-bridge', host=host, alias=alias)
        self.addCleanup(self.bridge.close)
        self.bridge.ipc = self.owner
        self.bridge.hosts.hosts = lambda: {host: {'alias': alias}}
        self.bridge.hosts.state = lambda: {}
        self.bridge.store = SimpleNamespace(get=self.metadata, history=self.history, alias=alias,
            list=lambda **kwargs: [], attached_parent=lambda identifier: None)
        requests = []
        def ssh_read(target_alias, source, **kwargs):
            self.assertEqual(target_alias, alias)
            requests.append(source)
            prefix = ('import subprocess, sys, shutil\n'
                      'native_popen = subprocess.Popen\n'
                      'shutil.which = lambda name: "fixture-remote-runtime"\n'
                      'def fixture_popen(command, **options):\n'
                      '    assert command == ["fixture-remote-runtime", "app-server", "--listen", "stdio://"]\n'
                      '    return native_popen(' + repr([sys.executable, str(self.runtime_script), self.target,
                                                        str(self.persisted), str(self.log)]) + ', **options)\n'
                      'subprocess.Popen = fixture_popen\n')
            process = self.native_popen([sys.executable, '-I', '-c', prefix + source],
                cwd=str(self.home), env={**os.environ, 'CODEX_HOME': str(self.home)},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            stdout, stderr = process.communicate(timeout=20)
            self.assertEqual(process.returncode, 0, stderr)
            return json.loads(stdout)
        with patch('bridge.app.service.ssh_read', side_effect=ssh_read):
            result = self.bridge.recover(self.source, self.recovery_body())
        self.assertEqual((result['status'], result['host'], result['id']),
                         ('confirmed', host, self.target), result)
        self.assertGreaterEqual(len(requests), 2)
        self.assertEqual(self.sent[0][:3], (self.target, 'source-model', 'high'))
        self.assertEqual(self.sent[0][3][0]['text'],
                         '读取会话[原任务](codex://threads/' + self.source + '?hostId=ssh%3Afixture-host)，继续完成')


if __name__ == '__main__':
    unittest.main()
