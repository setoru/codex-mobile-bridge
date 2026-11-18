"""App shortcut creation uses real creator I/O and a synthetic target owner."""
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
        if identifier == self.fixture.source:
            raise AssertionError('Shortcut creation must not connect the source conversation')
        self.followed.append(identifier)
        if not self.available:
            raise IPCError('Synthetic owner unavailable')
        self.publish(identifier)

    def publish(self, identifier):
        if identifier not in self.states:
            self.states[identifier] = self.fixture.history(identifier)
            # Desktop's composer may initially reopen with its own model defaults.
            self.states[identifier].update(latestModel='desktop-default', latestReasoningEffort='low')
            self.states[identifier]['latestThreadSettings'] = {'serviceTier': 'default'}
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
            raise AssertionError('Shortcut creation wrote to another conversation or owner')
        self.calls.append((method, copy.deepcopy(params)))
        state = self.states[identifier]
        if method == 'thread-follower-update-thread-settings':
            settings = params['threadSettings']
            if fixtures.PERMISSION_OVERRIDES.intersection(settings):
                raise AssertionError('Source permissions must not be applied to the target')
            if 'model' in settings:
                state['latestModel'] = settings['model']
            if 'effort' in settings:
                state['latestReasoningEffort'] = settings['effort']
            for key in ('model', 'effort', 'serviceTier'):
                if key in settings:
                    state.setdefault('latestThreadSettings', {})[key] = copy.deepcopy(settings[key])
            if self.fail_settings:
                raise IPCError('Synthetic settings response lost')
            self.publish(identifier)
            return {'result': {'applied': True}}
        if method == 'thread-follower-start-turn':
            raise AssertionError('The backend shortcut must not send a message')
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
        self.opened = []
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
        self.source_state = {
            'id': self.source, 'cwd': str(self.cwd), 'title': '原任务',
            'modelProvider': 'retired-source-provider', 'latestModel': 'source-model',
            'latestReasoningEffort': 'high', 'latestThreadSettings': {'serviceTier': 'fast'},
            'currentPermissions': {'activePermissionProfile': {'id': ':danger-full-access'},
                                   'approvalPolicy': 'never', 'approvalsReviewer': 'user',
                                   'runtimeWorkspaceRoots': ['/source-only-extra-root']},
            'turns': [], 'requests': [], 'historyComplete': True,
            'threadRuntimeStatus': {'type': 'notLoaded'},
        }
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
        thread = {'id': identifier, 'cwd': params['cwd'], 'modelProvider': 'fixture-provider',
                  'model': params.get('model', 'default-model'),
                  'reasoningEffort': (params.get('config') or {}).get('model_reasoning_effort', 'low'),
                  'serviceTier': params.get('serviceTier', 'default'),
                  'approvalPolicy': 'on-request', 'approvalsReviewer': 'user',
                  'activePermissionProfile': {'id': ':workspace'},
                  'sandbox': {'type': 'workspaceWrite', 'writableRoots': [params['cwd']], 'networkAccess': False},
                  'runtimeWorkspaceRoots': [params['cwd']], 'name': None, 'turns': []}
        result = {'thread': thread, **{key: thread[key] for key in
                  ('model', 'reasoningEffort', 'serviceTier', 'approvalPolicy', 'approvalsReviewer',
                   'activePermissionProfile', 'sandbox', 'runtimeWorkspaceRoots')}}
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
                'model_provider': ('retired-source-provider' if identifier == self.source else 'fixture-provider'),
                'title': '原任务' if identifier == self.source else ''}

    def history(self, identifier, **kwargs):
        self.metadata(identifier)
        if identifier == self.source:
            return copy.deepcopy(self.source_state)
        thread = json.loads(self.persisted.read_text())['thread']
        return {'id': identifier, 'cwd': thread['cwd'], 'title': '',
                'modelProvider': thread['modelProvider'], 'latestModel': thread['model'],
                'latestReasoningEffort': thread['reasoningEffort'],
                'latestThreadSettings': {'serviceTier': thread['serviceTier']},
                'currentPermissions': {key: copy.deepcopy(thread[key]) for key in
                    ('approvalPolicy', 'approvalsReviewer', 'activePermissionProfile', 'runtimeWorkspaceRoots')},
                'turns': [], 'requests': [], 'historyComplete': True,
                'threadRuntimeStatus': {'type': 'idle'}}

    def assert_creation_without_source_permissions(self, result):
        self.assertEqual(result['status'], 'created', result)
        self.assertEqual(result['id'], self.target)
        self.assertEqual(result['text'],
                         '读取会话[原任务](codex://threads/' + self.source + ')，继续完成')
        self.assertNotIn(self.source, self.owner.followed)
        for method, params in self.owner.calls:
            self.assertNotEqual(method, 'thread-follower-start-turn')
            if method == 'thread-follower-update-thread-settings':
                self.assertFalse(fixtures.PERMISSION_OVERRIDES.intersection(params['threadSettings']), params)
        start = next(row['params'] for row in json.loads(self.log.read_text()) if row['method'] == 'thread/start')
        self.assertFalse(fixtures.PERMISSION_OVERRIDES.intersection(start), start)
        self.assertNotIn('modelProvider', start)
        self.assertEqual(start['cwd'], str(self.cwd))
        self.assertEqual(start['model'], 'source-model')

    def test_app_creation_keeps_default_permissions_and_returns_unsent_text(self):
        original = copy.deepcopy(self.source_state)
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assert_creation_without_source_permissions(result)
        self.assertTrue(result['ready'], result)
        state = self.owner.states[self.target]
        self.assertEqual(state['latestModel'], 'source-model')
        self.assertEqual(state['latestReasoningEffort'], 'high')
        self.assertEqual(state['latestThreadSettings']['serviceTier'], 'fast')
        self.assertEqual(state['currentPermissions']['activePermissionProfile']['id'], ':workspace')
        self.assertEqual(state['currentPermissions']['runtimeWorkspaceRoots'], [str(self.cwd)])
        self.assertEqual(self.source_state, original)

    def test_missing_source_permissions_do_not_block_app_creation(self):
        self.source_state.pop('currentPermissions')
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assert_creation_without_source_permissions(result)
        self.assertTrue(result['ready'], result)

    def test_unknown_model_setting_keeps_id_and_text_without_replaying_on_status_read(self):
        self.source_state['latestThreadSettings'] = {}
        self.owner.fail_settings = True
        operation = str(uuid.uuid4())
        result = self.bridge.recover(self.source, self.recovery_body(operation))
        self.assert_creation_without_source_permissions(result)
        self.assertFalse(result['ready'], result)
        previous_updates = [method for method, _ in self.owner.calls].count('thread-follower-update-thread-settings')
        repeated = self.bridge.recover(self.source, self.recovery_body(operation))
        self.assertEqual((repeated['id'], repeated['text']), (result['id'], result['text']))
        self.assertEqual([method for method, _ in self.owner.calls].count('thread-follower-update-thread-settings'),
                         previous_updates)
        self.owner.fail_settings = False
        self.owner.publish(self.target)
        checked = self.bridge.recover(self.source, request_id=operation)
        self.assertEqual(checked['id'], self.target)
        self.assertEqual(checked['text'], result['text'])
        self.assertEqual(self.owner.states[self.target]['latestModel'], 'source-model')
        self.assertEqual([method for method, _ in self.owner.calls].count('thread-follower-update-thread-settings'),
                         previous_updates)
        methods = [row['method'] for row in json.loads(self.log.read_text())]
        self.assertEqual(methods.count('thread/start'), 1)

    def test_unavailable_app_keeps_created_chat_for_read_only_reconciliation(self):
        self.owner.available = False
        operation = str(uuid.uuid4())
        result = self.bridge.recover(self.source, self.recovery_body(operation))
        self.assert_creation_without_source_permissions(result)
        self.assertFalse(result['ready'], result)
        self.owner.available = True
        checked = self.bridge.recover(self.source, request_id=operation)
        self.assertEqual(checked['id'], self.target)
        self.assertIn('读取会话[', checked['text'])
        self.assertEqual([row['method'] for row in json.loads(self.log.read_text())].count('thread/start'), 1)

    def test_created_receipt_does_not_override_later_owner_settings(self):
        operation = str(uuid.uuid4())
        result = self.bridge.recover(self.source, self.recovery_body(operation))
        self.assert_creation_without_source_permissions(result)
        state = self.owner.states[self.target]
        state['latestModel'] = 'later-user-model'
        state['latestThreadSettings']['model'] = 'later-user-model'
        state['currentPermissions']['activePermissionProfile'] = {'id': 'later-user-permissions'}
        self.owner.publish(self.target)
        previous_calls = copy.deepcopy(self.owner.calls)
        checked = self.bridge.recover(self.source, self.recovery_body(operation))
        self.assertEqual((checked['id'], checked['text']), (result['id'], result['text']))
        self.assertEqual(self.owner.states[self.target]['latestModel'], 'later-user-model')
        self.assertEqual(self.owner.states[self.target]['currentPermissions']['activePermissionProfile']['id'],
                         'later-user-permissions')
        self.assertEqual(self.owner.calls, previous_calls)

    def test_remote_app_creation_runs_the_shared_creator_on_the_original_host(self):
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
                         ('created', host, self.target), result)
        self.assertTrue(result['ready'], result)
        self.assertTrue(requests)
        self.assertEqual(result['text'],
                         '读取会话[原任务](codex://threads/' + self.source + '?hostId=ssh%3Afixture-host)，继续完成')
        self.assertNotIn(self.source, self.owner.followed)
        self.assertFalse(any(method == 'thread-follower-start-turn' for method, _ in self.owner.calls))
        start = next(row['params'] for row in json.loads(self.log.read_text()) if row['method'] == 'thread/start')
        self.assertEqual(start['cwd'], str(self.cwd))
        self.assertFalse(fixtures.PERMISSION_OVERRIDES.intersection(start), start)
        self.assertNotIn('modelProvider', start)


if __name__ == '__main__':
    unittest.main()
