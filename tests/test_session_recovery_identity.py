"""Recovery checks the source account and endpoint before native writes."""
import base64
import json
import os
import unittest
from unittest.mock import patch

import test_session_recovery as fixtures


def native_login(principal, generation='initial'):
    claims = base64.urlsafe_b64encode(json.dumps({
        'sub': principal, 'email': principal + '@fixture.test', 'nonce': generation,
    }).encode()).decode().rstrip('=')
    return json.dumps({'auth_mode': 'chatgpt', 'tokens': {
        'account_id': 'shared-fixture-workspace', 'id_token': 'e30.' + claims + '.fixture',
    }})


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
            with self.assertRaisesRegex(ValueError, '原接入'):
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
        (self.home / 'auth.json').write_text(native_login('fixture-user'))
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

    def test_same_workspace_user_switch_rejects_the_original_menu_identity(self):
        self.native.threads[self.source]['modelProvider'] = 'openai'
        (self.home / 'config.toml').write_text('model_provider = "openai"\n')
        auth = self.home / 'auth.json'
        auth.write_text(native_login('first-user'))
        body = self.recovery_body()
        auth.write_text(native_login('second-user'))
        result = self.bridge.recover(self.source, body)
        self.assertEqual((result['status'], result['phase'], result['id']),
                         ('failed', 'validating', None), result)
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_changed_provider_headers_reject_the_captured_source_connection(self):
        config = self.home / 'config.toml'
        configuration = config.read_text() + (
            '[model_providers.fixture-provider.http_headers]\nX-Workspace = "workspace-a"\n')
        config.write_text(configuration)
        body = self.recovery_body()
        config.write_text(configuration.replace('workspace-a', 'workspace-b'))
        result = self.bridge.recover(self.source, body)
        self.assertEqual((result['status'], result['phase'], result['id']),
                         ('failed', 'validating', None), result)
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_changed_static_query_routing_rejects_the_captured_source_connection(self):
        config = self.home / 'config.toml'
        configuration = config.read_text() + (
            '[model_providers.fixture-provider.query_params]\nworkspace = "workspace-a"\n')
        config.write_text(configuration)
        body = self.recovery_body()
        config.write_text(configuration.replace('workspace-a', 'workspace-b'))
        result = self.bridge.recover(self.source, body)
        self.assertEqual((result['status'], result['phase'], result['id']),
                         ('failed', 'validating', None), result)
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_changed_project_configuration_rejects_the_captured_worktree_connection(self):
        for parent in (self.cwd, self.cwd.parent):
            with self.subTest(layer=parent):
                config = parent / '.codex' / 'config.toml'
                config.parent.mkdir(exist_ok=True)
                config.write_text('[model_providers.fixture-provider]\n'
                                  'base_url = "https://first-project.invalid/v1"\n')
                body = self.recovery_body()
                config.write_text('[model_providers.fixture-provider]\n'
                                  'base_url = "https://second-project.invalid/v1"\n')
                result = self.bridge.recover(self.source, body)
                self.assertEqual((result['status'], result['phase'], result['id']),
                                 ('failed', 'validating', None), result)
        self.assertEqual(self.native.threads[self.source]['cwd'], str(self.cwd))
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_same_login_principal_can_continue_after_native_token_renewal(self):
        self.native.threads[self.source]['modelProvider'] = 'openai'
        (self.home / 'config.toml').write_text('model_provider = "openai"\n')
        auth = self.home / 'auth.json'
        auth.write_text(native_login('fixture-user', 'initial-token'))
        body = self.recovery_body()
        auth.write_text(native_login('fixture-user', 'renewed-token'))
        result = self.bridge.recover(self.source, body)
        self.assertEqual(result['status'], 'confirmed', result)
        self.assertEqual([method for method, _ in self.native.calls].count('thread/start'), 1)
        self.assertEqual([method for method, _ in self.native.calls].count('turn/start'), 1)

    def test_unknown_original_header_environment_cannot_use_the_gateway_environment(self):
        config = self.home / 'config.toml'
        config.write_text(config.read_text() + (
            '[model_providers.fixture-provider.env_http_headers]\n'
            'X-Workspace = "FIXTURE_WORKSPACE"\n'))
        with patch.dict(os.environ, {'FIXTURE_WORKSPACE': 'fixture-gateway-workspace'}):
            with self.assertRaisesRegex(ValueError, '无法核验原接入配置'):
                self.bridge.recover(self.source)
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_unknown_external_provider_authentication_is_rejected_in_each_configuration_layer(self):
        home_config = self.home / 'config.toml'
        original = home_config.read_text()
        project_config = self.cwd / '.codex' / 'config.toml'
        project_config.parent.mkdir()
        for layer in ('home', 'profile', 'project'):
            for field in ('auth', 'gateway_oauth', 'aws'):
                with self.subTest(layer=layer, field=field):
                    home_config.write_text(original)
                    project_config.unlink(missing_ok=True)
                    definition = (f'[model_providers.fixture-provider]\n'
                                  'base_url = "https://fixture.invalid/v1"\n'
                                  'experimental_bearer_token = "fixture-only"\n'
                                  f'{field} = {{required = true}}\n')
                    if layer == 'home':
                        home_config.write_text(definition)
                    elif layer == 'profile':
                        home_config.write_text('profile = "selected"\n' + definition.replace(
                            '[model_providers.', '[profiles.selected.model_providers.'))
                    else:
                        project_config.write_text(definition)
                    with self.assertRaisesRegex(ValueError, '无法核验原接入配置'):
                        self.bridge.recover(self.source)
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_project_configuration_change_during_source_read_cannot_rebind_the_operation(self):
        config = self.cwd / '.codex' / 'config.toml'
        config.parent.mkdir()
        configuration = '[model_providers.fixture-provider.http_headers]\nX-Workspace = "workspace-a"\n'
        config.write_text(configuration)
        body = self.recovery_body()
        history = self.bridge.store.history

        def changed_project_configuration(sid, **kwargs):
            value = history(sid, **kwargs)
            if sid == self.source:
                config.write_text(configuration.replace('workspace-a', 'workspace-b'))
            return value

        self.bridge.store.history = changed_project_configuration
        result = self.bridge.recover(self.source, body)
        self.assertEqual((result['status'], result['phase'], result['id']),
                         ('failed', 'validating', None), result)
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])

    def test_missing_native_login_principal_cannot_create_a_chatgpt_recovery(self):
        self.native.threads[self.source]['modelProvider'] = 'openai'
        (self.home / 'config.toml').write_text('model_provider = "openai"\n')
        (self.home / 'auth.json').write_text(
            '{"auth_mode":"chatgpt","tokens":{"account_id":"fixture-workspace"}}')
        with self.assertRaisesRegex(ValueError, '账号身份'):
            self.bridge.recover(self.source)
        self.assertNotIn('thread/start', [method for method, _ in self.native.calls])
        self.assertNotIn('turn/start', [method for method, _ in self.native.calls])


if __name__ == '__main__':
    unittest.main()
