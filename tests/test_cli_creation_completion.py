"""Known CLI creation IDs are completed without repeating native writes."""
import unittest
import uuid
from unittest.mock import patch

from bridge.app.service import Bridge, LiveSession
from bridge.clients.codex.app_server import AppServerError, AppServerUnknownResult
from bridge.features.sessions.create import CreationError
from test_native_chat_titles import ChatFixture


class CliCreationCompletionTests(unittest.TestCase):
    def fixture(self, failure_method='thread/read', error=None):
        fixture = ChatFixture(self, 'cli')
        fixture.owner.store.home = fixture.native.home
        fixture.read_failure = error or AppServerError('Synthetic creation read unavailable')
        original = fixture.native.request

        def request(method, params, **options):
            if method == failure_method and fixture.read_failure is not None:
                fixture.native.calls.append((method, dict(params)))
                raise fixture.read_failure
            return original(method, params, **options)

        fixture.native.request = request
        return fixture

    def methods(self, fixture):
        return [method for method, _ in fixture.native.calls]

    def test_retry_finishes_known_thread_and_allows_first_send_without_recreating(self):
        fixture = self.fixture()
        request = str(uuid.uuid4())
        with self.assertRaises(CreationError):
            fixture.create('', request)
        entry = fixture.owner.creations[request]
        identifier = entry['id']
        self.assertNotEqual(entry.get('phase'), 'ready')
        self.assertEqual(entry['runtimeCwd'], fixture.native.threads[identifier]['cwd'])
        fixture.read_failure = None
        self.assertEqual(fixture.create(None, request), identifier)
        view = fixture.owner.view(identifier, attach=False)
        self.assertTrue(view['connected'])
        self.assertTrue(view['access']['canSend'])
        self.assertEqual(entry['phase'], 'ready')
        self.assertEqual(fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))['status'], 'accepted')
        self.assertEqual(self.methods(fixture).count('thread/start'), 1)
        self.assertEqual(self.methods(fixture).count('turn/start'), 1)
        self.assertNotIn('thread/resume', self.methods(fixture))
        self.assertNotIn('thread/name/set', self.methods(fixture))

    def test_persistent_read_failure_keeps_known_id_and_never_reports_success(self):
        fixture = self.fixture()
        request = str(uuid.uuid4())
        for _ in range(2):
            with self.assertRaises(CreationError):
                fixture.create('', request)
        self.assertIn(fixture.owner.creations[request]['id'], fixture.native.threads)
        self.assertNotEqual(fixture.owner.creations[request].get('phase'), 'ready')
        self.assertEqual(self.methods(fixture).count('thread/start'), 1)
        self.assertNotIn('turn/start', self.methods(fixture))
        self.assertNotIn('thread/resume', self.methods(fixture))

    def test_unknown_name_write_is_only_checked_on_retry(self):
        fixture = self.fixture('thread/name/set', AppServerUnknownResult('Synthetic name result unknown'))
        request = str(uuid.uuid4())
        with self.assertRaises(CreationError):
            fixture.create('Manual title', request)
        identifier = fixture.owner.creations[request]['id']
        fixture.read_failure = None
        with self.assertRaisesRegex(CreationError, '名称'):
            fixture.create('Manual title', request)
        fixture.native.threads[identifier]['name'] = 'Manual title'
        self.assertEqual(fixture.create('Manual title', request), identifier)
        self.assertEqual(self.methods(fixture).count('thread/start'), 1)
        self.assertEqual(self.methods(fixture).count('thread/name/set'), 1)
        self.assertNotIn('turn/start', self.methods(fixture))

    def test_model_verification_failure_cannot_be_bypassed_by_known_id(self):
        fixture = ChatFixture(self, 'cli')
        original = fixture.native.request

        def request(method, params, **options):
            result = original(method, params, **options)
            return {**result, 'model': 'unexpected-model'} if method == 'thread/start' else result

        fixture.native.request = request
        options = {'model': 'fixture-model', 'effort': 'low', 'modelCatalogIdentity': 'fixture-scope'}
        catalog = {'models': [{'id': 'fixture-model', 'efforts': ['low']}], 'cache': {'identity': 'fixture-scope'}}
        operation = str(uuid.uuid4())
        with patch.object(fixture.owner, 'creation_models', return_value=catalog):
            for _ in range(2):
                with self.assertRaises(CreationError):
                    fixture.owner.create_chat(fixture.project, '', operation, kind='cli', model_options=options)
        self.assertEqual(self.methods(fixture).count('thread/start'), 1)
        self.assertNotEqual(fixture.owner.creations[operation].get('phase'), 'ready')
        self.assertNotIn('thread/resume', self.methods(fixture))

    def test_lost_original_connection_cannot_complete_pending_creation(self):
        fixture = self.fixture()
        request = str(uuid.uuid4())
        with self.assertRaises(CreationError):
            fixture.create('', request)
        fixture.read_failure = None
        fixture.native.close()
        with self.assertRaises(CreationError):
            fixture.create('', request)
        self.assertEqual(self.methods(fixture).count('thread/start'), 1)
        self.assertNotIn('thread/resume', self.methods(fixture))

    def test_gateway_restart_does_not_resume_a_pending_creation(self):
        fixture = self.fixture()
        request = str(uuid.uuid4())
        with self.assertRaises(CreationError):
            fixture.create('', request)
        restarted = Bridge(fixture.native.home, fixture.owner.data_dir,
                           codex_bin=fixture.native.home / 'no-real-codex')
        self.addCleanup(restarted.close)
        restarted.cli_sessions.client = fixture.native
        restarted.hosts.state = lambda: {}
        restarted.hosts.hosts = lambda: {}
        restarted.store = fixture.owner.store
        fixture.read_failure = None
        with self.assertRaises(CreationError):
            restarted.create_chat(fixture.project, '', request, kind='cli')
        self.assertEqual(self.methods(fixture).count('thread/start'), 1)
        self.assertNotIn('thread/resume', self.methods(fixture))

    def test_completed_request_does_not_restore_original_name_or_model(self):
        fixture = ChatFixture(self, 'cli')
        fixture.owner.store.home = fixture.native.home
        request = str(uuid.uuid4())
        identifier = fixture.create('Original title', request)
        fixture.native.threads[identifier].update(name='Later native title', model='later-model')
        self.assertEqual(fixture.create('Original title', request), identifier)
        self.assertEqual(fixture.owner.view(identifier, attach=False)['title'], 'Later native title')
        self.assertEqual(self.methods(fixture).count('thread/start'), 1)
        self.assertEqual(self.methods(fixture).count('thread/name/set'), 1)
        self.assertNotIn('thread/settings/update', self.methods(fixture))

    def test_completed_receipt_remains_readable_after_its_proxy_disconnects(self):
        fixture = ChatFixture(self, 'cli')
        request = str(uuid.uuid4())
        identifier = fixture.create('', request)
        fixture.native.close()
        fixture.owner.cli_sessions._disconnect()
        self.assertEqual(fixture.create('', request), identifier)
        self.assertEqual(self.methods(fixture).count('thread/start'), 1)
        self.assertNotIn('thread/resume', self.methods(fixture))

    def test_completed_receipt_rejects_changed_saved_identity_after_disconnect(self):
        fixture = ChatFixture(self, 'cli')
        request = str(uuid.uuid4())
        identifier = fixture.create('', request)
        fixture.native.close()
        fixture.owner.cli_sessions._disconnect()
        original = fixture.owner.store.get
        fixture.owner.store.get = lambda sid: {**original(sid), 'model_provider': 'different-provider'}
        with self.assertRaises(CreationError):
            fixture.create('', request)
        self.assertEqual(fixture.owner.creations[request]['id'], identifier)
        self.assertNotIn('thread/resume', self.methods(fixture))

    def test_independent_cli_can_send_before_its_new_thread_is_in_inventory(self):
        from test_cli_empty_creation import CliEmptyCreationTests
        fixture = CliEmptyCreationTests.fixture(self)
        fixture.owner.host = 'codex:fixture'
        fixture.project = fixture.owner.host + '|independent'
        identifier = fixture.create('', str(uuid.uuid4()))
        self.assertTrue(fixture.owner.view(identifier, attach=False)['access']['canSend'])
        self.assertEqual(fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))['status'], 'accepted')
        self.assertEqual(self.methods(fixture).count('thread/start'), 1)
        self.assertEqual(self.methods(fixture).count('turn/start'), 1)

    def test_independent_cli_still_rejects_a_verified_desktop_source(self):
        from test_cli_empty_creation import CliEmptyCreationTests
        fixture = CliEmptyCreationTests.fixture(self)
        fixture.owner.host = 'codex:fixture'
        fixture.project = fixture.owner.host + '|independent'
        identifier = fixture.create('', str(uuid.uuid4()))
        fixture.native.persisted.add(identifier)
        original = fixture.owner.store.get
        fixture.owner.store.get = lambda sid: {**original(sid), 'origin': 'desktop'}
        with self.assertRaisesRegex(ValueError, '独立入口'):
            fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
        self.assertNotIn('turn/start', self.methods(fixture))

    def test_recovery_installs_fresh_cli_thread_before_inventory_is_written(self):
        from test_cli_empty_creation import CliEmptyCreationTests
        fixture = CliEmptyCreationTests.fixture(self)
        source = str(uuid.uuid4())
        fixture.native.threads[source] = {'id': source, 'cwd': str(fixture.native.home),
            'modelProvider': 'fixture-provider', 'model': 'fixture-model', 'name': None,
            'preview': 'Existing task', 'turns': [], 'status': {'type': 'idle'}}
        fixture.native.persisted.add(source)
        original_history = fixture.owner.store.history
        fixture.owner.store.history = lambda sid, **kwargs: {
            **original_history(sid, **kwargs), 'latestModel': 'fixture-model'}
        result = fixture.owner.recover(source, {'id': str(uuid.uuid4())})
        self.assertEqual(result['status'], 'created', result)
        self.assertTrue(result['ready'], result)
        self.assertNotEqual(result['id'], source)
        self.assertNotIn(result['id'], fixture.native.persisted)
        self.assertTrue(fixture.owner.view(result['id'], attach=False)['access']['canSend'])
        self.assertEqual(self.methods(fixture).count('thread/start'), 1)
        self.assertNotIn('turn/start', self.methods(fixture))

    def test_creation_completion_preserves_an_existing_desktop_owner(self):
        fixture = ChatFixture(self, 'cli')
        original = fixture.native.request
        desktop = {}

        def request(method, params, **options):
            result = original(method, params, **options)
            if method == 'thread/start':
                identifier = result['thread']['id']
                session = LiveSession(identifier)
                session.origin = 'desktop'
                session.origin_verified = True
                session.runtime = 'desktop'
                session.owner = 'original-desktop-owner'
                session.connected = True
                session.state = {'id': identifier, 'cwd': params['cwd'], 'title': 'Original desktop conversation'}
                fixture.owner.live[identifier] = session
                desktop['session'] = session
            return result

        fixture.native.request = request
        with self.assertRaisesRegex(CreationError, '归属'):
            fixture.create('', str(uuid.uuid4()))
        session = desktop['session']
        self.assertEqual(session.owner, 'original-desktop-owner')
        self.assertEqual(session.runtime, 'desktop')
        self.assertEqual(session.state['title'], 'Original desktop conversation')
        self.assertNotIn('turn/start', self.methods(fixture))


if __name__ == '__main__':
    unittest.main()
