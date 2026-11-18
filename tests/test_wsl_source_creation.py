"""Source-directory shortcuts use ordinary WSL creation without resuming the source."""
import copy
import json
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bridge.clients.codex.ipc import IPCError
from bridge.features.endpoints.registry import entry_id
from bridge.features.endpoints.wsl import WslEntry
from test_native_chat_titles import NativeRuntime


ROOT = Path(__file__).resolve().parents[1]


class SourceRuntime(NativeRuntime):
    def request(self, method, params, **kwargs):
        result = super().request(method, params, **kwargs)
        if method == 'thread/start':
            thread = self.threads[result['thread']['id']]
            thread.update(model=params.get('model', 'default-model'),
                          reasoningEffort=(params.get('config') or {}).get('model_reasoning_effort', 'low'),
                          serviceTier=params.get('serviceTier', 'default'),
                          activePermissionProfile={'id': ':workspace'}, approvalPolicy='on-request',
                          approvalsReviewer='user', sandbox={'type': 'workspaceWrite', 'networkAccess': False})
            result = {'thread': copy.deepcopy(thread), 'model': thread['model'],
                      'reasoningEffort': thread['reasoningEffort'], 'serviceTier': thread['serviceTier'],
                      **{key: copy.deepcopy(thread[key]) for key in
                         ('activePermissionProfile', 'approvalPolicy', 'approvalsReviewer', 'sandbox')}}
        return result


class WslSourceCreationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.cwd = self.home / 'worktree'
        self.cwd.mkdir()
        self.source = str(uuid.uuid4())
        self.native = SourceRuntime(self.home)
        self.native.threads[self.source] = {
            'id': self.source, 'cwd': str(self.cwd), 'modelProvider': 'old-provider',
            'model': 'source-model', 'reasoningEffort': 'high', 'serviceTier': 'fast',
            'name': '原任务 [Python]', 'preview': '', 'turns': [], 'status': {'type': 'idle'},
            'activePermissionProfile': {'id': ':danger-full-access'}, 'approvalPolicy': 'never',
            'approvalsReviewer': 'user', 'sandbox': {'type': 'dangerFullAccess'}}
        row = {'id': entry_id('Fixture', 'fixture', '/fixture/.codex'), 'distribution': 'Fixture',
               'user': 'fixture', 'home': '/fixture/.codex', 'label': 'Fixture WSL'}
        self.entry = WslEntry(row, self.home / 'data')
        self.addCleanup(self.entry.close)
        self.entry.server, self.entry.generation = self.native, 'fixture-generation'
        self.native.event = lambda message: self.entry._event(message, self.entry.generation)
        self.entry._account_route = Mock(return_value={'managed': False})
        self.entry.read = Mock(return_value={'cwd': str(self.cwd)})
        self.history_ids = []
        self.entry.store = SimpleNamespace(get=self.metadata, history=self.history, list=lambda **kwargs: [])

    def metadata(self, identifier):
        thread = self.native.threads[identifier]
        return {'id': identifier, 'origin': 'cli', 'cwd': thread['cwd'], 'title': thread['name'],
                'model_provider': thread['modelProvider'], 'model': thread.get('model')}

    def history(self, identifier, **kwargs):
        self.history_ids.append(identifier)
        thread = self.native.threads[identifier]
        return {'id': identifier, 'cwd': thread['cwd'], 'title': thread['name'],
                'latestModel': thread.get('model'), 'latestReasoningEffort': thread.get('reasoningEffort'),
                'latestThreadSettings': {'serviceTier': thread['serviceTier']}, 'turns': []}

    def test_cold_source_list_offers_recovery_without_loading_the_source(self):
        self.entry.store.list = lambda **kwargs: [self.metadata(self.source)]
        rows = self.entry.list()
        self.assertEqual([row['id'] for row in rows], [self.source])
        self.assertTrue(rows[0]['access']['canRecover'])
        self.assertTrue(rows[0]['access']['readOnly'])
        self.assertFalse(rows[0]['connected'])
        self.assertFalse(rows[0]['access']['canSend'])
        self.assertEqual(self.native.calls, [])
        self.assertEqual(self.history_ids, [])
        self.assertNotIn(self.source, self.entry.live)
        self.entry.read.assert_not_called()
        result = self.entry.recover(self.source, {'id': str(uuid.uuid4())})
        self.assertTrue(result['ready'], result)
        self.assertNotIn(self.source, self.entry.live)
        self.assertFalse(any(params.get('threadId') == self.source for _, params in self.native.calls))

    def test_source_shortcut_creates_in_original_worktree_with_default_permissions(self):
        original = copy.deepcopy(self.native.threads[self.source])
        attempt = str(uuid.uuid4())
        result = self.entry.recover(self.source, {'id': attempt})
        self.assertEqual(result['status'], 'created', result)
        self.assertTrue(result['ready'], result)
        self.assertNotEqual(result['id'], self.source)
        target = self.native.threads[result['id']]
        self.assertEqual(target['cwd'], str(self.cwd))
        self.assertEqual((target['model'], target['reasoningEffort'], target['serviceTier']),
                         ('source-model', 'high', 'fast'))
        self.assertEqual(target['modelProvider'], 'fixture-provider')
        self.assertEqual(target['activePermissionProfile']['id'], ':workspace')
        self.assertEqual(self.native.threads[self.source], original)
        self.assertNotIn(self.source, self.entry.live)
        self.assertEqual(self.history_ids.count(self.source), 1)
        methods = [method for method, _ in self.native.calls]
        self.assertNotIn('turn/start', methods)
        self.assertFalse(any(params.get('threadId') == self.source for _, params in self.native.calls))
        start = next(params for method, params in self.native.calls if method == 'thread/start')
        self.assertFalse(set(start) & {'modelProvider', 'permissions', 'approvalPolicy',
                                      'approvalsReviewer', 'runtimeWorkspaceRoots'})
        self.assertIn('读取会话[原任务 \\[Python\\]](codex://threads/' + self.source, result['text'])
        repeated = self.entry.recover(self.source, {'id': attempt})
        self.assertEqual(repeated['id'], result['id'])
        self.assertEqual(sum(method == 'thread/start' for method, _ in self.native.calls), 1)

    def test_missing_source_model_uses_new_chat_default_and_reports_warning(self):
        self.native.threads[self.source].pop('model')
        self.native.threads[self.source].pop('reasoningEffort')
        result = self.entry.recover(self.source, {'id': str(uuid.uuid4())})
        self.assertEqual(result['status'], 'created', result)
        self.assertTrue(result['ready'], result)
        self.assertTrue(result.get('warning'))
        self.assertEqual(self.native.threads[result['id']]['model'], 'default-model')
        start = next(params for method, params in self.native.calls if method == 'thread/start')
        self.assertNotIn('model', start)
        self.assertNotIn('config', start)

    def test_source_shortcut_preserves_valid_trailing_space_in_native_cwd(self):
        self.cwd = self.home / 'worktree '
        self.cwd.mkdir()
        self.assertTrue((self.home / 'worktree').is_dir())
        self.native.threads[self.source]['cwd'] = str(self.cwd)
        self.entry._draft_identity_cache['fixture-provider'] = (time.monotonic(), 'fixture-account')

        def read(distribution, script):
            process = subprocess.run([sys.executable, '-I', '-S', '-B', '-'],
                input=script, text=True, capture_output=True, cwd=self.home, timeout=5)
            self.assertEqual(process.returncode, 0, process.stderr)
            return json.loads(process.stdout)

        self.entry.read = Mock(side_effect=read)
        result = self.entry.recover(self.source, {'id': str(uuid.uuid4())})
        self.assertTrue(result['ready'], result)
        start = next(params for method, params in self.native.calls if method == 'thread/start')
        self.assertEqual(start['cwd'], str(self.cwd))
        self.assertEqual(self.native.threads[result['id']]['cwd'], str(self.cwd))

    def test_source_shortcut_preserves_an_explicitly_cleared_speed(self):
        self.native.threads[self.source]['serviceTier'] = None
        result = self.entry.recover(self.source, {'id': str(uuid.uuid4())})
        self.assertTrue(result['ready'], result)
        start = next(params for method, params in self.native.calls if method == 'thread/start')
        self.assertIn('serviceTier', start)
        self.assertIsNone(start['serviceTier'])
        self.assertIsNone(self.native.threads[result['id']]['serviceTier'])

    def test_source_and_project_creation_keep_id_when_native_confirmation_changes(self):
        self.entry.store.list = lambda **kwargs: [self.metadata(self.source)]
        original_request = self.native.request
        for surface in ('source', 'project'):
            for failure in ('generation', 'saved-id'):
                with self.subTest(surface=surface, failure=failure):
                    attempt = str(uuid.uuid4())
                    def changed(method, params, **kwargs):
                        result = original_request(method, params, **kwargs)
                        if failure == 'generation' and method == 'thread/start':
                            self.entry.generation = str(uuid.uuid4())
                        if failure == 'saved-id' and method == 'thread/read' and params.get('includeTurns'):
                            result['thread']['id'] = str(uuid.uuid4())
                        return result
                    self.native.request = changed
                    first_call = len(self.native.calls)
                    if surface == 'source':
                        result = self.entry.recover(self.source, {'id': attempt})
                        self.assertEqual(result['status'], 'created', result)
                        self.assertFalse(result['ready'])
                    else:
                        with self.assertRaisesRegex(IPCError, '不会重复创建'):
                            self.entry.create_chat(self.entry.host + '|' + str(self.cwd), '', attempt)
                    self.assertIn(self.entry.creations[attempt]['id'], self.native.threads)
                    calls = self.native.calls[first_call:]
                    self.assertFalse(any(method in ('thread/resume', 'turn/start') for method, _ in calls))
                    self.assertNotIn(self.source, self.entry.live)
        self.native.request = original_request

    def test_unconfirmed_model_keeps_new_chat_and_text_without_sending(self):
        native_request = self.native.request
        def unconfirmed(method, params, **kwargs):
            result = native_request(method, params, **kwargs)
            if method == 'thread/start':
                result['model'] = 'different-model'
            return result
        self.native.request = unconfirmed
        result = self.entry.recover(self.source, {'id': str(uuid.uuid4())})
        self.assertEqual(result['status'], 'created', result)
        self.assertFalse(result['ready'])
        self.assertIn(result['id'], self.native.threads)
        self.assertTrue(result['text'])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_unknown_creation_is_not_replayed_but_new_attempt_can_create(self):
        native_request = self.native.request
        def lose_reply(method, params, **kwargs):
            result = native_request(method, params, **kwargs)
            if method == 'thread/start':
                raise IPCError('Synthetic creation reply lost')
            return result
        self.native.request = lose_reply
        attempt = str(uuid.uuid4())
        result = self.entry.recover(self.source, {'id': attempt})
        self.assertEqual(result['status'], 'unknown', result)
        self.assertIsNone(result['id'])
        self.native.request = native_request
        repeated = self.entry.recover(self.source, {'id': attempt})
        self.assertEqual(repeated['status'], 'unknown')
        self.assertEqual(sum(method == 'thread/start' for method, _ in self.native.calls), 1)
        fresh = self.entry.recover(self.source, {'id': str(uuid.uuid4())})
        self.assertTrue(fresh['ready'], fresh)
        self.assertEqual(sum(method == 'thread/start' for method, _ in self.native.calls), 2)

    def test_closed_entry_rejects_source_creation_before_native_write(self):
        self.entry.closed.set()
        with self.assertRaises(IPCError):
            self.entry.recover(self.source, {'id': str(uuid.uuid4())})
        self.assertEqual(self.native.calls, [])
