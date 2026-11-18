"""Source metadata does not override the selected native creation context."""
import copy
import unittest
from unittest.mock import Mock

import test_session_recovery as fixtures


class RecoveryIdentityTests(unittest.TestCase):
    setUp = fixtures.SessionRecoveryTests.setUp
    make_bridge = fixtures.SessionRecoveryTests.make_bridge
    metadata = fixtures.SessionRecoveryTests.metadata
    history = fixtures.SessionRecoveryTests.history
    recovery_body = fixtures.SessionRecoveryTests.recovery_body
    assert_created_without_source_operations = fixtures.SessionRecoveryTests.assert_created_without_source_operations

    def test_source_provider_and_authentication_are_not_copied(self):
        source = self.native.threads[self.source]
        source['modelProvider'] = 'obsolete-provider-not-configured'
        original = copy.deepcopy(source)
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assert_created_without_source_operations(result)
        target = self.native.threads[result['id']]
        self.assertEqual(target['modelProvider'], fixtures.RecoveryRuntime.DEFAULT_PROVIDER)
        self.assertEqual(target['model'], original['model'])
        self.assertEqual(self.native.threads[self.source], original)
        creation = next(params for method, params in self.native.calls if method == 'thread/start')
        self.assertNotIn('modelProvider', creation)

    def test_missing_source_provider_does_not_block_native_defaults(self):
        self.native.threads[self.source].pop('modelProvider')
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assert_created_without_source_operations(result)
        self.assertEqual(self.native.threads[result['id']]['modelProvider'],
                         fixtures.RecoveryRuntime.DEFAULT_PROVIDER)

    def test_external_auth_configuration_does_not_require_a_recovery_parser(self):
        (self.home / 'config.toml').write_text(
            'model_provider = "fixture-provider"\n[model_providers.fixture-provider]\n'
            'base_url = "https://fixture.invalid/v1"\n'
            'auth = {required = true}\naws = {region = "fixture-region"}\n'
            'env_key = "SOURCE_DAEMON_ONLY_KEY"\n'
            '[model_providers.fixture-provider.env_http_headers]\n'
            'X-Workspace = "SOURCE_DAEMON_ONLY_WORKSPACE"\n')
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assert_created_without_source_operations(result)
        creation = next(params for method, params in self.native.calls if method == 'thread/start')
        self.assertFalse({'auth', 'aws', 'env_key', 'modelProvider'}.intersection(creation), creation)

    def test_current_native_provider_is_used_with_the_inherited_model(self):
        self.native.DEFAULT_PROVIDER = 'selected-native-provider'
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assert_created_without_source_operations(result)
        self.assertEqual((self.native.threads[result['id']]['modelProvider'],
                          self.native.threads[result['id']]['model']),
                         ('selected-native-provider', 'source-model'))

    def test_idle_status_does_not_read_source_or_authentication(self):
        self.bridge.store.get = Mock(side_effect=AssertionError('Status must not read the source'))
        self.bridge.store.history = Mock(side_effect=AssertionError('Status must not read the source'))
        result = self.bridge.recover(self.source)
        self.assertEqual(result['status'], 'idle', result)
        self.assertIsNone(result['id'])
        self.bridge.store.get.assert_not_called()
        self.bridge.store.history.assert_not_called()
        self.assertEqual(self.native.calls, [])

    def test_legacy_source_context_cannot_rebind_an_existing_creation(self):
        request = self.recovery_body()
        result = self.bridge.recover(self.source, {**request, 'sourceContext': 'old-account-context'})
        self.assert_created_without_source_operations(result)
        repeated = self.bridge.recover(self.source, {**request, 'sourceContext': 'different-account-context'})
        self.assertEqual((repeated['id'], repeated['text']), (result['id'], result['text']))
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])


if __name__ == '__main__':
    unittest.main()
