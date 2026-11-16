"""Recover into a new chat through Bridge, with only native I/O substituted."""
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
from unittest.mock import Mock, patch

from bridge.app.service import Bridge
from test_native_chat_titles import NativeRuntime


ROOT = Path(__file__).resolve().parents[1]


class RecoveryRuntime(NativeRuntime):
    def request(self, method, params, **kwargs):
        if method == 'thread/start':
            result = super().request(method, params, **kwargs)
            thread = self.threads[result['thread']['id']]
            thread.update(model=params.get('model', 'default-model'),
                          modelProvider=params.get('modelProvider', 'fixture-provider'),
                          reasoningEffort=(params.get('config') or {}).get('model_reasoning_effort', 'low'))
            result.update(thread=copy.deepcopy(thread), model=thread['model'],
                          reasoningEffort=thread['reasoningEffort'])
            for key in ('serviceTier', 'approvalPolicy', 'approvalsReviewer', 'runtimeWorkspaceRoots'):
                if key in params:
                    result[key] = copy.deepcopy(params[key])
                    thread[key] = copy.deepcopy(params[key])
            if 'permissions' in params:
                result['activePermissionProfile'] = {'id': params['permissions']}
                thread['activePermissionProfile'] = result['activePermissionProfile']
            return result
        return super().request(method, params, **kwargs)


class SessionRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.cwd = self.home / 'worktree'
        self.cwd.mkdir()
        (self.home / 'config.toml').write_text(
            'model_provider = "fixture-provider"\n[model_providers.fixture-provider]\n'
            'base_url = "https://fixture.invalid/v1"\nexperimental_bearer_token = "fixture-only"\n')
        self.native = RecoveryRuntime(self.home)
        self.source = str(uuid.uuid4())
        self.native.threads[self.source] = {
            'id': self.source, 'cwd': str(self.cwd), 'modelProvider': 'fixture-provider',
            'model': 'source-model', 'reasoningEffort': 'high', 'name': '原任务 [Python]',
            'preview': '', 'turns': [], 'status': {'type': 'idle'}, 'canAcceptDirectInput': True,
            'serviceTier': 'fast', 'approvalPolicy': 'never', 'approvalsReviewer': 'user',
            'activePermissionProfile': {'id': ':danger-full-access'}}
        self.bridge = self.make_bridge()

    def make_bridge(self):
        bridge = Bridge(self.home, self.home / 'bridge', codex_bin=self.home / 'no-real-codex')
        self.addCleanup(bridge.close)
        bridge.cli_sessions.client = self.native
        self.native.event = bridge.cli_sessions._event
        bridge.ipc.connect = Mock(side_effect=AssertionError('Recovery of CLI must not open App'))
        bridge.hosts.state = lambda: {}
        bridge.hosts.hosts = lambda: {}
        bridge.store = SimpleNamespace(get=self.metadata, history=self.history,
                                       list=lambda **kwargs: [], attached_parent=lambda sid: None)
        bridge.cli_sessions.store = bridge.store
        return bridge

    def metadata(self, sid):
        thread = self.native.threads[sid]
        return {'id': sid, 'origin': 'cli', 'cwd': thread['cwd'], 'model_provider': thread['modelProvider'],
                'title': thread['name'] or thread['preview'], 'model': thread['model']}

    def history(self, sid, **kwargs):
        thread = self.native.threads[sid]
        return {'id': sid, 'title': thread['name'] or thread['preview'], 'cwd': thread['cwd'],
                'modelProvider': thread['modelProvider'], 'latestModel': thread['model'],
                'latestReasoningEffort': thread['reasoningEffort'],
                'latestThreadSettings': {'serviceTier': thread.get('serviceTier')},
                'currentPermissions': {key: thread[key] for key in
                    ('approvalPolicy', 'approvalsReviewer', 'activePermissionProfile') if key in thread},
                'turns': [], 'requests': [], 'historyComplete': True,
                'persistedUserMessageIds': thread.get('acceptedIds', []),
                'threadRuntimeStatus': {'type': 'notLoaded'}}

    def recovery_body(self, operation_id=None, bridge=None):
        bridge = bridge or self.bridge
        identifier = operation_id or str(uuid.uuid4())
        contexts = self.__dict__.setdefault('_recovery_contexts', {})
        key = (bridge.host, identifier)
        if key not in contexts:
            contexts[key] = bridge.recover(self.source, request_id=operation_id)['sourceContext']
        return {'id': identifier, 'sourceContext': contexts[key]}

    def test_cli_recovery_creates_in_source_worktree_and_sends_with_source_settings(self):
        original = copy.deepcopy(self.native.threads[self.source])
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assertEqual(result['status'], 'confirmed', result)
        self.assertNotEqual(result['id'], self.source)
        target = self.native.threads[result['id']]
        self.assertEqual(target['cwd'], str(self.cwd))
        self.assertEqual((target['model'], target['reasoningEffort'], target['modelProvider']),
                         ('source-model', 'high', 'fixture-provider'))
        self.assertEqual(target['serviceTier'], 'fast')
        self.assertEqual(target['activePermissionProfile']['id'], ':danger-full-access')
        self.assertEqual(target['turns'][0]['items'][0]['content'][0]['text'],
                         '读取会话[原任务 \\[Python\\]](codex://threads/' + self.source + ')，继续完成')
        self.assertEqual(self.native.threads[self.source], original)
        self.assertNotIn('thread/name/set', [method for method, _ in self.native.calls])
        self.assertFalse(any(params.get('threadId') == self.source for _, params in self.native.calls))

    def test_unknown_send_is_only_reconciled_from_fresh_receipts_without_replay(self):
        operation = str(uuid.uuid4())
        self.native.fail_send = True
        result = self.bridge.recover(self.source, self.recovery_body(operation))
        self.assertEqual(result['status'], 'unknown', result)
        self.assertIsNotNone(result['id'])
        self.native.fail_send = False
        for request in (operation, str(uuid.uuid4())):
            duplicate = self.bridge.recover(self.source, self.recovery_body(request))
            self.assertEqual((duplicate['id'], duplicate['operationId'], duplicate['status']),
                             (result['id'], operation, 'unknown'))
        self.native.threads[result['id']]['acceptedIds'] = [operation]
        confirmed = self.bridge.recover(self.source, request_id=operation)
        self.assertEqual(confirmed['status'], 'confirmed', confirmed)
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)
        self.assertEqual([method for method, _ in self.native.calls].count('turn/start'), 1)

    def test_remote_cli_keeps_host_and_checks_directory_on_the_target(self):
        self.bridge.close()
        self.native.process = object()
        bridge = Bridge(self.home, self.home / 'remote-data', host='ssh-fixture', alias='fixture')
        self.addCleanup(bridge.close)
        bridge.cli_sessions.client = self.native
        self.native.event = bridge.cli_sessions._event
        bridge.store = SimpleNamespace(get=self.metadata, history=self.history, alias='fixture')
        bridge.cli_sessions.store = bridge.store
        bridge.hosts.hosts = lambda: {'ssh-fixture': {'alias': 'fixture'}}
        calls = []
        def ssh(alias, source, **kwargs):
            self.assertEqual(alias, 'fixture')
            calls.append(source)
            return json.loads(subprocess.check_output([sys.executable, '-B', '-c', source],
                              env={**os.environ, 'CODEX_HOME': str(self.home)}, text=True))
        with patch('bridge.app.service.ssh_read', side_effect=ssh):
            result = bridge.recover(self.source, self.recovery_body(bridge=bridge))
        self.assertEqual(result['status'], 'confirmed', result)
        self.assertEqual(result['host'], 'ssh-fixture')
        self.assertIn('?hostId=ssh-fixture)，继续完成', result['text'])
        self.assertTrue(calls)
        self.assertEqual(self.native.threads[result['id']]['cwd'], str(self.cwd))

    def test_bridge_managed_api_account_uses_its_shared_native_authentication(self):
        self.native.threads[self.source]['modelProvider'] = 'bridge_api'
        (self.home / 'config.toml').write_text(
            'model_provider = "bridge_api"\n[model_providers.bridge_api]\n'
            'base_url = "https://fixture.invalid/v1"\nrequires_openai_auth = true\n')
        (self.home / 'auth.json').write_text(json.dumps({'auth_mode': 'apikey', 'OPENAI_API_KEY': 'fixture-key'}))
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assertEqual(result['status'], 'confirmed', result)
        self.assertEqual(self.native.threads[result['id']]['modelProvider'], 'bridge_api')


if __name__ == '__main__':
    unittest.main()
