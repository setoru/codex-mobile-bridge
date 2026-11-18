"""Creation receipts prevent replay without imposing source execution settings."""
import copy
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor

from bridge.clients.codex.app_server import AppServerUnknownResult
import test_session_recovery as fixtures


class SessionRecoverySafetyTests(unittest.TestCase):
    setUp = fixtures.SessionRecoveryTests.setUp
    make_bridge = fixtures.SessionRecoveryTests.make_bridge
    metadata = fixtures.SessionRecoveryTests.metadata
    recovery_body = fixtures.SessionRecoveryTests.recovery_body
    assert_created_without_source_operations = fixtures.SessionRecoveryTests.assert_created_without_source_operations

    def history(self, sid, **kwargs):
        state = fixtures.SessionRecoveryTests.history(self, sid, **kwargs)
        if 'sandboxPolicy' in self.native.threads[sid]:
            state['currentPermissions']['sandboxPolicy'] = copy.deepcopy(self.native.threads[sid]['sandboxPolicy'])
        return state

    def restart(self):
        self.bridge.close()
        self.native.process, self.native.closed = object(), False
        self.bridge = self.make_bridge()

    def test_lost_creation_reply_is_not_replayed_after_restart(self):
        native_request = self.native.request
        def lose_creation_reply(method, params, **kwargs):
            result = native_request(method, params, **kwargs)
            if method == 'thread/start':
                raise AppServerUnknownResult('Synthetic lost creation reply')
            return result
        self.native.request = lose_creation_reply
        operation_id = str(uuid.uuid4())
        result = self.bridge.recover(self.source, self.recovery_body(operation_id))
        self.assertEqual(result['status'], 'unknown', result)
        self.assertIsNone(result['id'])
        self.restart()
        for repeated in (self.bridge.recover(self.source, self.recovery_body(operation_id)),
                         self.bridge.recover(self.source, request_id=operation_id)):
            self.assertEqual((repeated['operationId'], repeated['status'], repeated['id']),
                             (operation_id, 'unknown', None), repeated)
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_new_explicit_id_can_create_while_an_older_creation_is_unknown(self):
        native_request = self.native.request
        lost = []
        def lose_first_reply(method, params, **kwargs):
            result = native_request(method, params, **kwargs)
            if method == 'thread/start' and not lost:
                lost.append(True)
                raise AppServerUnknownResult('Synthetic lost first creation reply')
            return result
        self.native.request = lose_first_reply
        first = self.bridge.recover(self.source, self.recovery_body())
        self.assertEqual(first['status'], 'unknown', first)
        second = self.bridge.recover(self.source, self.recovery_body())
        self.assert_created_without_source_operations(second)
        self.assertNotEqual(first['operationId'], second['operationId'])
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 2)

    def test_in_progress_duplicate_and_status_reads_do_not_queue_another_creation(self):
        native_request = self.native.request
        started, finish = threading.Event(), threading.Event()
        def delay_creation(method, params, **kwargs):
            if method == 'thread/start':
                started.set()
                if not finish.wait(5):
                    raise AssertionError('Fixture creation was not released')
            return native_request(method, params, **kwargs)
        self.native.request = delay_creation
        operation_id = str(uuid.uuid4())
        with ThreadPoolExecutor(max_workers=3) as callers:
            creating = callers.submit(self.bridge.recover, self.source, self.recovery_body(operation_id))
            self.assertTrue(started.wait(2))
            status = callers.submit(self.bridge.recover, self.source, request_id=operation_id)
            repeated = callers.submit(self.bridge.recover, self.source, self.recovery_body(operation_id))
            try:
                for pending in (status.result(timeout=2), repeated.result(timeout=2)):
                    self.assertEqual((pending['operationId'], pending['status']),
                                     (operation_id, 'creating'), pending)
            finally:
                finish.set()
            complete = creating.result(timeout=2)
        self.assert_created_without_source_operations(complete)
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)

    def test_model_confirmation_failure_keeps_id_and_text_without_recreation(self):
        native_request = self.native.request
        def unconfirmed_model(method, params, **kwargs):
            result = native_request(method, params, **kwargs)
            if method == 'thread/start':
                result['model'] = 'unconfirmed-model'
            return result
        self.native.request = unconfirmed_model
        operation_id = str(uuid.uuid4())
        result = self.bridge.recover(self.source, self.recovery_body(operation_id))
        self.assertIn(result['status'], ('created', 'unknown'), result)
        self.assertIn(result['id'], self.native.threads)
        self.assertFalse(result['ready'], result)
        self.assertIn('读取会话[', result['text'])
        self.restart()
        repeated = self.bridge.recover(self.source, self.recovery_body(operation_id))
        self.assertEqual((repeated['id'], repeated['text']), (result['id'], result['text']))
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_workspace_read_only_and_custom_source_permissions_do_not_override_defaults(self):
        permissions = [
            {'activePermissionProfile': {'id': ':workspace'}, 'approvalPolicy': 'on-request',
             'sandboxPolicy': {'type': 'workspaceWrite', 'writableRoots': ['/source-extra'], 'networkAccess': True}},
            {'activePermissionProfile': {'id': ':read-only'}, 'approvalPolicy': 'untrusted',
             'sandboxPolicy': {'type': 'readOnly'}},
            {'activePermissionProfile': {'id': 'custom-source-profile'}, 'approvalPolicy': 'on-failure',
             'sandboxPolicy': {'type': 'custom', 'fixtureRule': 'source-only'}},
        ]
        for settings in permissions:
            with self.subTest(settings=settings):
                source = self.native.threads[self.source]
                source.update(settings)
                original = copy.deepcopy(source)
                result = self.bridge.recover(self.source, self.recovery_body())
                self.assert_created_without_source_operations(result)
                target = self.native.threads[result['id']]
                self.assertEqual(target['activePermissionProfile']['id'], ':workspace')
                self.assertFalse(target['sandbox']['networkAccess'])
                self.assertEqual(target['sandbox']['writableRoots'], [str(self.cwd)])
                self.assertEqual(self.native.threads[self.source], original)
        for method, params in self.native.calls:
            if method == 'thread/start':
                self.assertFalse(fixtures.PERMISSION_OVERRIDES.intersection(params), params)

    def test_same_operation_id_cannot_change_source(self):
        request = self.recovery_body()
        result = self.bridge.recover(self.source, request)
        self.assert_created_without_source_operations(result)
        other = str(uuid.uuid4())
        self.native.threads[other] = {**copy.deepcopy(self.native.threads[self.source]), 'id': other}
        with self.assertRaisesRegex(ValueError, '不同|同一'):
            self.bridge.recover(other, request)
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)

    def test_source_information_is_read_once_and_not_replaced_on_retry(self):
        history = self.bridge.store.history
        reads = []
        def read_once(sid, **kwargs):
            if sid == self.source:
                reads.append(sid)
            return history(sid, **kwargs)
        self.bridge.store.history = read_once
        request = self.recovery_body()
        result = self.bridge.recover(self.source, request)
        self.assert_created_without_source_operations(result)
        self.native.threads[self.source].update(name='Later source name', model='later-source-model')
        for repeated in (self.bridge.recover(self.source, request),
                         self.bridge.recover(self.source, request_id=request['id'])):
            self.assertEqual((repeated['id'], repeated['text']), (result['id'], result['text']))
        self.assertEqual(len(reads), 1)
        self.assertEqual(self.native.threads[result['id']]['model'], 'source-model')
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)

    def test_created_receipt_does_not_override_later_target_settings(self):
        request = self.recovery_body()
        result = self.bridge.recover(self.source, request)
        self.assert_created_without_source_operations(result)
        target = self.native.threads[result['id']]
        target.update(model='later-user-model', activePermissionProfile={'id': 'later-user-permissions'})
        previous_creations = [method for method, _ in self.native.calls].count('thread/start')
        repeated = self.bridge.recover(self.source, request)
        self.assertEqual((repeated['id'], repeated['text']), (result['id'], result['text']))
        self.assertEqual(target['model'], 'later-user-model')
        self.assertEqual(target['activePermissionProfile']['id'], 'later-user-permissions')
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), previous_creations)
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_missing_source_directory_refuses_creation(self):
        self.native.threads[self.source].pop('cwd')
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assertEqual(result['status'], 'failed', result)
        self.assertIsNone(result['id'])
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])

    def test_native_creation_returning_source_id_never_exposes_or_writes_source(self):
        native_request = self.native.request
        original = copy.deepcopy(self.native.threads[self.source])
        def return_source_id(method, params, **kwargs):
            result = native_request(method, params, **kwargs)
            if method == 'thread/start':
                result['thread']['id'] = self.source
            return result
        self.native.request = return_source_id
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assertNotEqual(result['id'], self.source, result)
        self.assertEqual(self.native.threads[self.source], original)
        self.assertFalse(any(params.get('threadId') == self.source for _, params in self.native.calls))
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_every_new_explicit_id_can_create_another_chat(self):
        first = self.bridge.recover(self.source, self.recovery_body())
        second = self.bridge.recover(self.source, self.recovery_body())
        self.assert_created_without_source_operations(first)
        self.assert_created_without_source_operations(second)
        self.assertNotEqual(first['id'], second['id'])
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 2)


if __name__ == '__main__':
    unittest.main()
