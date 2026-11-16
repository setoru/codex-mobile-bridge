"""Recovery checks the source account and endpoint before native writes."""
import os
import unittest
from unittest.mock import patch

import test_session_recovery as fixtures


class RecoveryIdentityTests(unittest.TestCase):
    setUp = fixtures.SessionRecoveryTests.setUp
    make_bridge = fixtures.SessionRecoveryTests.make_bridge
    metadata = fixtures.SessionRecoveryTests.metadata
    history = fixtures.SessionRecoveryTests.history
    recovery_body = fixtures.SessionRecoveryTests.recovery_body

    def test_openai_endpoint_change_during_source_read_cannot_use_another_route(self):
        self.native.threads[self.source]['modelProvider'] = 'openai'
        (self.home / 'auth.json').write_text(
            '{"auth_mode":"apikey","OPENAI_API_KEY":"fixture-key"}')
        config = self.home / 'config.toml'
        config.write_text('openai_base_url = "https://first.invalid/v1"\n')
        history = self.bridge.store.history

        def changed_endpoint(sid, **kwargs):
            value = history(sid, **kwargs)
            if sid == self.source:
                config.write_text('openai_base_url = "https://second.invalid/v1"\n')
            return value

        self.bridge.store.history = changed_endpoint
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assertEqual((result['status'], result['phase'], result['id']),
                         ('failed', 'validating', None), result)
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_missing_explicit_environment_key_cannot_use_another_shared_account(self):
        (self.home / 'auth.json').write_text(
            '{"auth_mode":"apikey","OPENAI_API_KEY":"another-fixture-key"}')
        (self.home / 'config.toml').write_text(
            '[model_providers.fixture-provider]\n'
            'base_url = "https://fixture.invalid/v1"\n'
            'env_key = "FIXTURE_REQUIRED_KEY"\nrequires_openai_auth = true\n')
        environment = {key: value for key, value in os.environ.items()
                       if key != 'FIXTURE_REQUIRED_KEY'}
        with patch.dict(os.environ, environment, clear=True):
            with self.assertRaisesRegex(ValueError, '账号身份'):
                self.bridge.recover(self.source)
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_changed_openai_configuration_endpoint_rejects_a_captured_menu(self):
        self.native.threads[self.source]['modelProvider'] = 'openai'
        (self.home / 'auth.json').write_text(
            '{"auth_mode":"apikey","OPENAI_API_KEY":"fixture-key"}')
        configurations = (
            'openai_base_url = "{endpoint}"\n',
            'profile = "selected"\nopenai_base_url = "https://default.invalid/v1"\n'
            '[profiles.selected]\nopenai_base_url = "{endpoint}"\n',
            'openai_base_url = "https://default.invalid/v1"\n'
            '[model_providers.openai]\nbase_url = "{endpoint}"\n',
        )
        for configuration in configurations:
            with self.subTest(configuration=configuration):
                config = self.home / 'config.toml'
                config.write_text(configuration.format(endpoint='https://first.invalid/v1'))
                body = self.recovery_body()
                config.write_text(configuration.format(endpoint='https://second.invalid/v1'))
                result = self.bridge.recover(self.source, body)
                self.assertEqual((result['status'], result['phase'], result['id']),
                                 ('failed', 'validating', None), result)
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_shared_provider_alias_preserves_the_official_chatgpt_account(self):
        self.native.threads[self.source]['modelProvider'] = 'bridge_api'
        (self.home / 'auth.json').write_text(
            '{"auth_mode":"chatgpt","tokens":{"account_id":"fixture-account"}}')
        (self.home / 'config.toml').write_text(
            'model_provider = "openai"\n[model_providers.bridge_api]\n'
            'name = "OpenAI"\nwire_api = "responses"\n'
            'requires_openai_auth = true\nsupports_websockets = true\n')
        result = self.bridge.recover(self.source, self.recovery_body())
        self.assertEqual(result['status'], 'confirmed', result)
        target = self.native.threads[result['id']]
        self.assertEqual(target['modelProvider'], 'bridge_api')
        self.assertEqual(target['turns'][0]['items'][0]['content'][0]['text'],
                         '读取会话[原任务 \\[Python\\]](codex://threads/' + self.source + ')，继续完成')
        self.assertFalse(any(params.get('threadId') == self.source
                             for _, params in self.native.calls))

    def test_api_account_without_a_verified_endpoint_cannot_use_the_gateway_route(self):
        self.native.threads[self.source]['modelProvider'] = 'openai'
        (self.home / 'auth.json').write_text(
            '{"auth_mode":"apikey","OPENAI_API_KEY":"fixture-home-key"}')
        (self.home / 'config.toml').write_text('model_provider = "openai"\n')
        with patch.dict(os.environ, {'OPENAI_BASE_URL': 'https://unverified.invalid/v1',
                                    'OPENAI_API_KEY': 'fixture-gateway-key'}):
            with self.assertRaisesRegex(ValueError, '账号身份'):
                self.bridge.recover(self.source)
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])


if __name__ == '__main__':
    unittest.main()
