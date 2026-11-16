"""Recovery interruption and identity guarantees through the Bridge interface."""
import unittest
import uuid
import threading
import json
import copy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from bridge.clients.codex.app_server import AppServerUnknownResult
from bridge.features.accounts.accounts import Accounts
import test_session_recovery as fixtures


class SessionRecoverySafetyTests(unittest.TestCase):
    setUp = fixtures.SessionRecoveryTests.setUp
    metadata = fixtures.SessionRecoveryTests.metadata

    def history(self, sid, **kwargs):
        state = fixtures.SessionRecoveryTests.history(self, sid, **kwargs)
        if 'sandboxPolicy' in self.native.threads[sid]:
            state['currentPermissions']['sandboxPolicy'] = self.native.threads[sid]['sandboxPolicy']
        return state

    def make_bridge(self):
        bridge = fixtures.SessionRecoveryTests.make_bridge(self)
        bridge.accounts = Accounts(bridge)
        return bridge

    def restart(self):
        self.bridge.close()
        self.native.process = object()
        self.native.closed = False
        self.bridge = self.make_bridge()

    @contextmanager
    def change_before_send(self, operation_id, change):
        replace = Path.replace
        changed = []

        def replace_then_change(path, target):
            result = replace(path, target)
            if Path(target) == self.home / 'bridge' / 'creations.json' and not changed:
                status = json.loads(Path(target).read_text())[operation_id]
                if status['status'] == 'running' and status['phase'] == 'sending':
                    changed.append(status['id'])
                    change(status['id'])
            return result

        with patch.object(Path, 'replace', replace_then_change):
            yield
        self.assertEqual(len(changed), 1, 'The fixture must change the environment before the first send')

    def change_native_model(self, sid, model):
        self.native.threads[sid]['model'] = model
        self.native.event({'method': 'thread/settings/updated', 'params': {
            'threadId': sid, 'threadSettings': {
                'model': model, 'effort': 'high', 'serviceTier': 'fast', 'approvalPolicy': 'never',
                'approvalsReviewer': 'user', 'activePermissionProfile': {'id': ':danger-full-access'},
                'sandboxPolicy': {'type': 'dangerFullAccess'}}}})

    def test_lost_creation_reply_without_id_is_not_replayed_after_restart(self):
        native_request = self.native.request

        def lose_creation_reply(method, params, **kwargs):
            result = native_request(method, params, **kwargs)
            if method == 'thread/start':
                raise AppServerUnknownResult('Synthetic lost creation reply')
            return result

        self.native.request = lose_creation_reply
        operation_id = str(uuid.uuid4())
        result = self.bridge.recover(self.source, {'id': operation_id})
        self.assertEqual((result['status'], result['phase'], result['id']), ('unknown', 'creating', None))
        self.restart()
        for request_id in (operation_id, str(uuid.uuid4())):
            repeated = self.bridge.recover(self.source, {'id': request_id})
            self.assertEqual((repeated['operationId'], repeated['status'], repeated['id']),
                             (operation_id, 'unknown', None))
        status = self.bridge.recover(self.source, request_id=operation_id)
        self.assertEqual(status['status'], 'unknown')
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_pending_recovery_allows_status_reads_and_reuses_a_reopened_menu_request(self):
        native_request = self.native.request
        started, finish = threading.Event(), threading.Event()

        def delay_creation(method, params, **kwargs):
            if method == 'thread/start':
                started.set()
                if not finish.wait(5):
                    raise AssertionError('The test did not release native creation')
            return native_request(method, params, **kwargs)

        self.native.request = delay_creation
        operation_id = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=3) as callers:
            creating = callers.submit(self.bridge.recover, self.source, {'id': operation_id})
            self.assertTrue(started.wait(2))
            status = callers.submit(self.bridge.recover, self.source, request_id=operation_id)
            reopened = callers.submit(self.bridge.recover, self.source, {'id': str(uuid.uuid4())})
            try:
                pending = status.result(timeout=2)
                self.assertEqual((pending['operationId'], pending['status'], pending['phase']),
                                 (operation_id, 'running', 'creating'))
                duplicate = reopened.result(timeout=2)
                self.assertEqual((duplicate['operationId'], duplicate['status']), (operation_id, 'running'))
            finally:
                finish.set()
            complete = creating.result(timeout=2)
        self.assertEqual(complete['status'], 'confirmed', complete)
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)
        self.assertEqual([method for method, _ in self.native.calls].count('turn/start'), 1)

    def test_unconfirmed_creation_keeps_known_id_without_replay(self):
        native_request = self.native.request

        def unconfirmed_model(method, params, **kwargs):
            result = native_request(method, params, **kwargs)
            if method == 'thread/start':
                result['model'] = 'unconfirmed-model'
            return result

        self.native.request = unconfirmed_model
        operation_id = str(uuid.uuid4())
        result = self.bridge.recover(self.source, {'id': operation_id})
        self.assertEqual((result['status'], result['phase']), ('unknown', 'creating'))
        self.assertIn(result['id'], self.native.threads)
        self.assertNotEqual(result['id'], self.source)
        self.restart()
        for request_id in (operation_id, str(uuid.uuid4())):
            repeated = self.bridge.recover(self.source, {'id': request_id})
            self.assertEqual((repeated['operationId'], repeated['status'], repeated['id']),
                             (operation_id, 'unknown', result['id']))
        self.assertEqual(self.bridge.recover(self.source, request_id=operation_id)['id'], result['id'])
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_conflicting_source_profile_and_sandbox_cannot_create_a_new_chat(self):
        self.native.threads[self.source]['sandboxPolicy'] = {
            'type': 'workspace-write', 'writableRoots': [str(self.cwd)], 'networkAccess': False}
        result = self.bridge.recover(self.source, {'id': str(uuid.uuid4())})
        self.assertEqual((result['status'], result['phase'], result['id']), ('failed', 'validating', None))
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_same_permission_profile_cannot_silently_broaden_the_effective_sandbox(self):
        source = self.native.threads[self.source]
        source.update(activePermissionProfile={'id': ':workspace'}, approvalPolicy='on-request',
                      sandboxPolicy={'type': 'workspaceWrite', 'writableRoots': [str(self.cwd)],
                                     'networkAccess': False})
        request = self.native.request
        def broader_sandbox(method, params, **kwargs):
            result = request(method, params, **kwargs)
            if method == 'thread/start':
                result['sandbox'] = {**source['sandboxPolicy'], 'networkAccess': True}
            return result
        self.native.request = broader_sandbox
        result = self.bridge.recover(self.source, {'id': str(uuid.uuid4())})
        self.assertEqual(result['status'], 'not_submitted', result)
        self.assertIsNotNone(result['id'])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_account_change_during_source_read_cannot_become_the_creation_identity(self):
        history = self.bridge.store.history
        config = self.home / 'config.toml'

        def history_with_account_change(sid, **kwargs):
            value = history(sid, **kwargs)
            if sid == self.source:
                config.write_text(config.read_text().replace('fixture-only', 'different-fixture-account'))
            return value

        self.bridge.store.history = history_with_account_change
        result = self.bridge.recover(self.source, {'id': str(uuid.uuid4())})
        self.assertEqual((result['status'], result['phase'], result['id']), ('failed', 'validating', None))
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_model_change_before_first_send_preserves_the_new_chat_for_explicit_continuation(self):
        operation_id = str(uuid.uuid4())
        with self.change_before_send(operation_id, lambda sid: self.change_native_model(sid, 'changed-model')):
            result = self.bridge.recover(self.source, {'id': operation_id})
        self.assertEqual((result['status'], result['phase']), ('not_submitted', 'sending'), result)
        self.assertIn(result['id'], self.native.threads)
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])
        self.change_native_model(result['id'], 'source-model')
        continued = self.bridge.recover(self.source, {'id': operation_id})
        self.assertEqual((continued['status'], continued['id']), ('confirmed', result['id']), continued)
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)
        self.assertEqual([method for method, _ in self.native.calls].count('turn/start'), 1)

    def test_account_change_before_first_send_is_not_submitted_and_can_resume_after_restoration(self):
        operation_id = str(uuid.uuid4())
        config = self.home / 'config.toml'
        original = config.read_text()
        with self.change_before_send(operation_id, lambda sid: config.write_text(
                original.replace('fixture-only', 'different-fixture-account'))):
            result = self.bridge.recover(self.source, {'id': operation_id})
        self.assertEqual((result['status'], result['phase']), ('not_submitted', 'sending'), result)
        self.assertIn(result['id'], self.native.threads)
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])
        config.write_text(original)
        continued = self.bridge.recover(self.source, {'id': operation_id})
        self.assertEqual((continued['status'], continued['id']), ('confirmed', result['id']), continued)
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)
        self.assertEqual([method for method, _ in self.native.calls].count('turn/start'), 1)

    def test_replaced_working_directory_blocks_first_send_without_losing_the_new_chat(self):
        operation_id = str(uuid.uuid4())
        original_directory = self.home / 'original-worktree'

        def replace_directory(sid):
            self.cwd.rename(original_directory)
            self.cwd.mkdir()

        with self.change_before_send(operation_id, replace_directory):
            result = self.bridge.recover(self.source, {'id': operation_id})
        self.assertEqual((result['status'], result['phase']), ('not_submitted', 'sending'), result)
        self.assertIn(result['id'], self.native.threads)
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])
        self.cwd.rmdir()
        original_directory.rename(self.cwd)
        continued = self.bridge.recover(self.source, {'id': operation_id})
        self.assertEqual((continued['status'], continued['id']), ('confirmed', result['id']), continued)
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)
        self.assertEqual([method for method, _ in self.native.calls].count('turn/start'), 1)

    def test_validation_failure_without_creation_does_not_block_removing_the_entry(self):
        self.native.threads[self.source]['activePermissionProfile'] = {'id': ':unavailable-fixture-profile'}
        result = self.bridge.recover(self.source, {'id': str(uuid.uuid4())})
        self.assertEqual((result['status'], result['phase'], result['id']), ('failed', 'validating', None))
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertIsNone(self.bridge.assert_detachable())
        self.restart()
        self.assertIsNone(self.bridge.assert_detachable())

    def test_native_creation_returning_the_source_id_never_sends_to_the_source(self):
        native_request = self.native.request
        original_source = copy.deepcopy(self.native.threads[self.source])

        def return_source_id(method, params, **kwargs):
            result = native_request(method, params, **kwargs)
            if method == 'thread/start':
                result['thread']['id'] = self.source
            return result

        self.native.request = return_source_id
        result = self.bridge.recover(self.source, {'id': str(uuid.uuid4())})
        self.assertEqual((result['status'], result['phase'], result['id']), ('unknown', 'creating', None))
        self.assertEqual(self.native.threads[self.source], original_source)
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_a_new_explicit_operation_after_completion_can_create_a_second_chat(self):
        first_operation, second_operation = str(uuid.uuid4()), str(uuid.uuid4())
        first = self.bridge.recover(self.source, {'id': first_operation})
        self.assertEqual(first['status'], 'confirmed', first)
        second = self.bridge.recover(self.source, {'id': second_operation})
        self.assertEqual(second['status'], 'confirmed', second)
        self.assertNotEqual(first['id'], second['id'])
        repeated = self.bridge.recover(self.source, {'id': first_operation})
        self.assertEqual((repeated['operationId'], repeated['id'], repeated['status']),
                         (first_operation, first['id'], 'confirmed'))
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 2)
        self.assertEqual([method for method, _ in self.native.calls].count('turn/start'), 2)


if __name__ == '__main__':
    unittest.main()
