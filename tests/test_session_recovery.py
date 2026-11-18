"""Create a chat from source metadata through Bridge, with synthetic native I/O."""
import copy
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.app.service import Bridge
from bridge.features.sessions.store import SessionStore
from test_native_chat_titles import NativeRuntime


ROOT = Path(__file__).resolve().parents[1]
PERMISSION_OVERRIDES = {
    'permissions', 'approvalPolicy', 'approvalsReviewer', 'runtimeWorkspaceRoots',
    'activePermissionProfile', 'sandbox', 'sandboxPolicy',
}


class RecoveryRuntime(NativeRuntime):
    DEFAULT_MODEL = 'default-model'
    DEFAULT_PROVIDER = 'fixture-provider'

    def request(self, method, params, **kwargs):
        if method == 'thread/start':
            result = super().request(method, params, **kwargs)
            thread = self.threads[result['thread']['id']]
            thread.update(
                model=params.get('model', self.DEFAULT_MODEL),
                modelProvider=params.get('modelProvider', self.DEFAULT_PROVIDER),
                reasoningEffort=(params.get('config') or {}).get('model_reasoning_effort', 'low'),
                serviceTier=params.get('serviceTier', 'default'),
                approvalPolicy=params.get('approvalPolicy', 'on-request'),
                approvalsReviewer=params.get('approvalsReviewer', 'user'),
                activePermissionProfile={'id': params.get('permissions', ':workspace')},
                sandbox={'type': 'workspaceWrite', 'writableRoots': [params['cwd']], 'networkAccess': False},
                runtimeWorkspaceRoots=copy.deepcopy(params.get('runtimeWorkspaceRoots', [params['cwd']])),
            )
            if thread['activePermissionProfile']['id'] == ':danger-full-access':
                thread['sandbox'] = {'type': 'dangerFullAccess'}
            result.update(thread=copy.deepcopy(thread), model=thread['model'],
                          reasoningEffort=thread['reasoningEffort'])
            for key in ('serviceTier', 'approvalPolicy', 'approvalsReviewer', 'activePermissionProfile',
                        'sandbox', 'runtimeWorkspaceRoots'):
                result[key] = copy.deepcopy(thread[key])
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
            'id': self.source, 'cwd': str(self.cwd), 'modelProvider': 'retired-source-provider',
            'model': 'source-model', 'reasoningEffort': 'high', 'name': '原任务 [Python]',
            'preview': '', 'turns': [], 'status': {'type': 'idle'}, 'canAcceptDirectInput': True,
            'serviceTier': 'fast', 'approvalPolicy': 'never', 'approvalsReviewer': 'user',
            'activePermissionProfile': {'id': ':danger-full-access'},
            'runtimeWorkspaceRoots': ['/source-only-extra-root'],
        }
        self.bridge = self.make_bridge()

    def make_bridge(self):
        bridge = Bridge(self.home, self.home / 'bridge', codex_bin=self.home / 'no-real-codex')
        self.addCleanup(bridge.close)
        bridge.cli_sessions.client = self.native
        self.native.event = bridge.cli_sessions._event
        bridge.ipc.connect = Mock(side_effect=AssertionError('CLI creation must not open App'))
        bridge.hosts.state = lambda: {}
        bridge.hosts.hosts = lambda: {}
        bridge.store = SimpleNamespace(get=self.metadata, history=self.history,
                                       list=lambda **kwargs: [], attached_parent=lambda sid: None)
        bridge.cli_sessions.store = bridge.store
        return bridge

    def metadata(self, sid):
        thread = self.native.threads[sid]
        return {'id': sid, 'origin': 'cli', 'cwd': thread.get('cwd'),
                'model_provider': thread.get('modelProvider'),
                'title': thread.get('name') or thread.get('preview'),
                **({'model': thread['model']} if thread.get('model') else {})}

    def history(self, sid, **kwargs):
        thread = self.native.threads[sid]
        return {'id': sid, 'title': thread.get('name') or thread.get('preview'), 'cwd': thread.get('cwd'),
                'modelProvider': thread.get('modelProvider'), 'latestModel': thread.get('model'),
                'latestReasoningEffort': thread.get('reasoningEffort'),
                'latestThreadSettings': {'serviceTier': thread['serviceTier']} if 'serviceTier' in thread else {},
                'currentPermissions': {key: copy.deepcopy(thread[key]) for key in
                    ('approvalPolicy', 'approvalsReviewer', 'activePermissionProfile', 'runtimeWorkspaceRoots')
                    if key in thread},
                'turns': [], 'requests': [], 'historyComplete': True,
                'threadRuntimeStatus': {'type': 'notLoaded'}}

    def recovery_body(self, operation_id=None, bridge=None):
        return {'id': operation_id or str(uuid.uuid4())}

    def assert_created_without_source_operations(self, result):
        self.assertEqual(result['status'], 'created', result)
        self.assertTrue(result['ready'], result)
        self.assertNotEqual(result['id'], self.source)
        self.assertEqual(self.native.threads[result['id']]['turns'], [])
        methods = [method for method, _ in self.native.calls]
        self.assertNotIn('turn/start', methods)
        self.assertNotIn('thread/resume', methods)
        self.assertNotIn('thread/name/set', methods)
        self.assertFalse(any(params.get('threadId') == self.source for _, params in self.native.calls))
        self.bridge.ipc.connect.assert_not_called()

    def test_cli_recovery_keeps_source_worktree_and_model_with_default_permissions(self):
        original = copy.deepcopy(self.native.threads[self.source])
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assert_created_without_source_operations(result)
        target = self.native.threads[result['id']]
        self.assertEqual(target['cwd'], str(self.cwd))
        self.assertEqual((target['model'], target['reasoningEffort'], target['modelProvider']),
                         ('source-model', 'high', RecoveryRuntime.DEFAULT_PROVIDER))
        self.assertEqual(target['serviceTier'], 'fast')
        self.assertEqual(target['activePermissionProfile']['id'], ':workspace')
        self.assertEqual(target['approvalPolicy'], 'on-request')
        self.assertEqual(target['runtimeWorkspaceRoots'], [str(self.cwd)])
        self.assertEqual(result['text'],
                         '读取会话[原任务 \\[Python\\]](codex://threads/' + self.source + ')，继续完成')
        self.assertEqual(self.native.threads[self.source], original)
        creation = next(params for method, params in self.native.calls if method == 'thread/start')
        self.assertFalse(PERMISSION_OVERRIDES.intersection(creation), creation)
        self.assertNotIn('modelProvider', creation)

    def test_missing_source_permissions_still_creates_without_sending(self):
        source = self.native.threads[self.source]
        for key in PERMISSION_OVERRIDES:
            source.pop(key, None)
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assert_created_without_source_operations(result)
        self.assertEqual(self.native.threads[result['id']]['activePermissionProfile']['id'], ':workspace')

    def test_recovery_accepts_an_operation_id_without_source_context(self):
        result = self.bridge.recover(self.source, {'id': str(uuid.uuid4())})
        self.assert_created_without_source_operations(result)

    def test_legacy_source_context_is_ignored(self):
        result = self.bridge.recover(self.source, {
            'id': str(uuid.uuid4()), 'sourceContext': 'obsolete-context-from-another-account',
        })
        self.assert_created_without_source_operations(result)

    def test_real_saved_history_without_permissions_can_create(self):
        rollout = self.home / 'sessions' / 'source.jsonl'
        rollout.parent.mkdir()
        records = [
            {'type': 'event_msg', 'payload': {'type': 'task_started', 'turn_id': 'source-turn'}},
            {'type': 'turn_context', 'payload': {
                'turn_id': 'source-turn', 'model': 'saved-model', 'effort': 'high', 'service_tier': 'fast',
                'approval_policy': 'never', 'sandbox_policy': {'type': 'dangerFullAccess'},
            }},
            {'type': 'response_item', 'payload': {
                'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': 'Source task'}],
            }},
            {'type': 'event_msg', 'payload': {'type': 'task_complete', 'turn_id': 'source-turn'}},
        ]
        rollout.write_text('\n'.join(json.dumps(record) for record in records) + '\n')
        database = self.home / 'state_5.sqlite'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, updated_at INT, '
                               'archived INT, originator TEXT, source TEXT, rollout_path TEXT, model_provider TEXT)')
            connection.execute('INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?)',
                (self.source, 'Saved source', str(self.cwd), 1, 0, 'codex_cli_rs', 'cli',
                 str(rollout), 'retired-source-provider'))
        store = SessionStore(self.home)
        original_files = (database.read_bytes(), rollout.read_bytes())
        saved = store.history(self.source, turn_limit=1)
        self.assertNotIn('currentPermissions', saved)
        original_history = self.bridge.store.history
        self.bridge.store.get = lambda sid: store.get(sid) if sid == self.source else self.metadata(sid)
        source_reads = []
        def history(sid, **kwargs):
            if sid == self.source:
                source_reads.append(sid)
                return store.history(sid, **kwargs)
            return original_history(sid, **kwargs)
        self.bridge.store.history = history
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assert_created_without_source_operations(result)
        target = self.native.threads[result['id']]
        self.assertEqual((target['cwd'], target['model'], target['reasoningEffort'], target['serviceTier']),
                         (str(self.cwd), 'saved-model', 'high', 'fast'))
        self.assertEqual(len(source_reads), 1)
        self.assertIn('读取会话[Saved source]', result['text'])
        self.assertEqual((database.read_bytes(), rollout.read_bytes()), original_files)

    def test_missing_source_model_uses_native_defaults_and_warns(self):
        source = self.native.threads[self.source]
        for key in ('model', 'modelProvider', 'reasoningEffort', 'serviceTier'):
            source.pop(key, None)
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assert_created_without_source_operations(result)
        self.assertTrue(result.get('warning'), result)
        self.assertEqual(self.native.threads[result['id']]['model'], RecoveryRuntime.DEFAULT_MODEL)
        creation = next(params for method, params in self.native.calls if method == 'thread/start')
        self.assertFalse({'model', 'modelProvider', 'config', 'serviceTier'}.intersection(creation), creation)

    def test_remote_cli_creates_on_the_selected_host_without_sending(self):
        self.bridge.close()
        self.native.process, self.native.closed = object(), False
        bridge = Bridge(self.home, self.home / 'remote-data', host='ssh-fixture', alias='fixture')
        self.addCleanup(bridge.close)
        bridge.cli_sessions.client = self.native
        self.native.event = bridge.cli_sessions._event
        bridge.store = SimpleNamespace(get=self.metadata, history=self.history, alias='fixture',
                                       list=lambda **kwargs: [], attached_parent=lambda sid: None)
        bridge.cli_sessions.store = bridge.store
        bridge.hosts.hosts = lambda: {'ssh-fixture': {'alias': 'fixture'}}
        def ssh(alias, source, **kwargs):
            self.assertEqual(alias, 'fixture')
            return json.loads(subprocess.check_output([sys.executable, '-B', '-c', source],
                              env={**os.environ, 'CODEX_HOME': str(self.home)}, text=True))
        with patch('bridge.app.service.ssh_read', side_effect=ssh):
            result = bridge.recover(self.source, self.recovery_body(bridge=bridge))
        self.assertEqual(result['status'], 'created', result)
        self.assertTrue(result['ready'], result)
        self.assertEqual(result['host'], 'ssh-fixture')
        self.assertIn('?hostId=ssh-fixture)，继续完成', result['text'])
        self.assertEqual(self.native.threads[result['id']]['cwd'], str(self.cwd))
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])


if __name__ == '__main__':
    unittest.main()
