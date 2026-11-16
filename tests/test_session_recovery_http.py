import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import test_bridge


class RecoveryHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.record = test_bridge.password_record('correct password test')

    setUp = test_bridge.HttpTests.setUp
    tearDown = test_bridge.HttpTests.tearDown
    request = test_bridge.HttpTests.request
    login = test_bridge.HttpTests.login

    def test_recovery_status_reads_selected_host_without_loading_the_source(self):
        operation_id = str(uuid.uuid4())
        result = {'operationId': operation_id, 'status': 'unknown', 'phase': 'sending',
                  'id': str(uuid.uuid4()), 'host': 'remote'}
        remote = SimpleNamespace(recover=Mock(return_value=result),
                                 session=Mock(side_effect=AssertionError('Source activation is forbidden')),
                                 view=Mock(side_effect=AssertionError('Source loading is forbidden')))
        self.server.bridge.for_host = Mock(return_value=remote)
        path = '/api/sessions/' + test_bridge.THREAD + '/recover?host=remote&id=' + operation_id
        self.assertEqual(self.request('GET', path)[0], 401)
        remote.recover.assert_not_called()
        headers = self.login()
        status, _, value = self.request('GET', path, headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(value, result)
        self.server.bridge.for_host.assert_called_once_with('remote')
        remote.recover.assert_called_once_with(test_bridge.THREAD, request_id=operation_id)
        remote.session.assert_not_called()
        remote.view.assert_not_called()

    def test_recovery_submission_requires_auth_csrf_origin_and_the_captured_source_context(self):
        result = {'operationId': str(uuid.uuid4()), 'status': 'confirmed', 'phase': 'complete',
                  'id': str(uuid.uuid4()), 'host': 'local'}
        self.server.bridge.recover = Mock(return_value=result)
        path = '/api/sessions/' + test_bridge.THREAD + '/recover?host=local'
        body = {'id': result['operationId'], 'sourceContext': '0123456789abcdef' * 4}
        self.assertEqual(self.request('POST', path, body)[0], 401)
        headers = self.login()
        self.assertEqual(self.request('POST', path, body, {'Cookie': headers['Cookie']})[0], 403)
        self.assertEqual(self.request('POST', path, body, {**headers, 'Origin': 'https://other.test'})[0], 403)
        for invalid in ({}, {'id': body['id']}, {'sourceContext': body['sourceContext']},
                        {**body, 'id': 42}, {**body, 'id': ''}, {**body, 'id': 'not-a-uuid'},
                        {**body, 'sourceContext': None}, {**body, 'sourceContext': 42},
                        {**body, 'sourceContext': ''}, {**body, 'sourceContext': 'a' * 63},
                        {**body, 'sourceContext': 'a' * 65}, {**body, 'sourceContext': 'A' * 64},
                        {**body, 'sourceContext': 'g' * 64},
                        {**body, 'model': 'untrusted'}, {**body, 'sourceId': str(uuid.uuid4())}):
            with self.subTest(body=invalid):
                self.assertEqual(self.request('POST', path, invalid, headers)[0], 400)
        self.server.bridge.recover.assert_not_called()
        status, _, value = self.request('POST', path, body, headers)
        self.assertEqual(status, 200)
        self.assertEqual(value, result)
        self.server.bridge.recover.assert_called_once_with(test_bridge.THREAD, body)

    def test_unsupported_and_disabled_entries_cannot_start_recovery(self):
        path = '/api/sessions/' + test_bridge.THREAD + '/recover?host=wsl:fixture'
        headers = self.login()
        unsupported = SimpleNamespace()
        self.server.bridge.for_host = Mock(return_value=unsupported)
        body = {'id': str(uuid.uuid4()), 'sourceContext': '0123456789abcdef' * 4}
        for method in ('GET', 'POST'):
            with self.subTest(method=method):
                status, _, value = self.request(method, path, body if method == 'POST' else None, headers)
                self.assertEqual(status, 400)
                self.assertIn('暂不支持从会话恢复', value['error'])
        unsupported.recover = Mock(return_value={'status': 'confirmed'})
        manager = self.server.desktop_sessions = SimpleNamespace(
            require_enabled=Mock(side_effect=ValueError('此应用已关闭，请在应用管理中开启')))
        self.assertEqual(self.request('POST', path, body, headers)[0], 400)
        manager.require_enabled.assert_called_once_with('wsl:fixture')
        unsupported.recover.assert_not_called()

    def test_recovery_status_accepts_latest_or_one_valid_operation_id(self):
        result = {'status': 'idle', 'sourceContext': '0123456789abcdef' * 4}
        self.server.bridge.recover = Mock(return_value=result)
        path = '/api/sessions/' + test_bridge.THREAD + '/recover'
        headers = self.login()
        status, _, value = self.request('GET', path, headers=headers)
        self.assertEqual(status, 200)
        self.assertEqual(value, result)
        self.server.bridge.recover.assert_called_once_with(test_bridge.THREAD, request_id=None)
        self.server.bridge.recover.reset_mock()
        operation_id = str(uuid.uuid4())
        for query in ('?id=', '?id=not-a-uuid', '?id=' + operation_id + '&id=' + str(uuid.uuid4())):
            with self.subTest(query=query):
                self.assertEqual(self.request('GET', path + query, headers=headers)[0], 400)
        self.server.bridge.recover.assert_not_called()


if __name__ == '__main__':
    unittest.main()
