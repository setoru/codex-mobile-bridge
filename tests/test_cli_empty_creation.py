"""An empty CLI thread remains usable when the daemon cannot list turns yet."""
import copy
import threading
import unittest
import uuid
from unittest.mock import Mock

from bridge.clients.codex.app_server import (
    AppServerError, AppServerUnavailable, AppServerUnknownResult, AppServerUnsupported)
from bridge.clients.codex.ipc import IPCError
from test_native_chat_titles import ChatFixture


class CliEmptyCreationTests(unittest.TestCase):
    def fixture(self):
        fixture = ChatFixture(self, 'cli')
        fixture.owner.store.home = fixture.native.home
        fixture.native.persisted = set()
        original_request = fixture.native.request
        original_metadata = fixture.metadata
        original_history = fixture.history

        def request(method, params, **kwargs):
            if method == 'thread/read' and params.get('includeTurns'):
                fixture.native.calls.append((method, copy.deepcopy(params)))
                raise AppServerUnsupported('list_turns is not supported yet')
            result = original_request(method, params, **kwargs)
            if method == 'thread/read' and not params.get('includeTurns'):
                result['thread']['turns'] = []
            if method == 'turn/start':
                fixture.native.persisted.add(params['threadId'])
            return result

        def metadata(identifier):
            if identifier not in fixture.native.persisted:
                raise KeyError('找不到这个 Codex 会话')
            return original_metadata(identifier)

        def history(identifier, **options):
            if identifier not in fixture.native.persisted:
                raise KeyError('找不到这个 Codex 会话')
            return original_history(identifier, **options)

        fixture.native.request = request
        fixture.owner.store.get = metadata
        fixture.owner.store.history = history
        return fixture

    def test_unnamed_creation_can_open_and_send_before_saved_inventory_catches_up(self):
        fixture = self.fixture()
        request, submission = str(uuid.uuid4()), str(uuid.uuid4())
        identifier = fixture.create('', request)
        self.assertNotIn(identifier, fixture.native.persisted)
        self.assertEqual(fixture.create(None, request), identifier)
        self.assertEqual(fixture.create('   ', request), identifier)
        initial = fixture.owner.view(identifier, attach=False)
        self.assertEqual(initial['title'], '未命名聊天')
        self.assertTrue(initial['access']['canSend'])
        timeline = fixture.owner.timeline_read(identifier)
        self.assertEqual(timeline['rows'], [])
        self.assertEqual(timeline['meta']['title'], '未命名聊天')
        self.assertTrue(timeline['meta']['access']['canSend'])
        self.assertEqual(fixture.owner.send(identifier, 'First message', submission)['status'], 'accepted')
        self.assertEqual(fixture.owner.send(identifier, 'First message', submission)['status'], 'accepted')
        self.assertIn(identifier, fixture.native.persisted)
        self.assertEqual(fixture.owner.store.get(identifier)['id'], identifier)
        methods = [method for method, _ in fixture.native.calls]
        self.assertEqual(methods.count('thread/start'), 1)
        self.assertEqual(methods.count('turn/start'), 1)
        self.assertNotIn('thread/name/set', methods)
        self.assertEqual(set(fixture.native.threads), {identifier})

    def test_metadata_only_refresh_preserves_turns_and_their_items(self):
        fixture = self.fixture()
        identifier = fixture.create('', str(uuid.uuid4()))
        sessions = fixture.owner.cli_sessions
        sessions._queue_refresh = lambda identifier: None
        completed = {'id': 'completed-turn', 'status': 'completed', 'items': [
            {'id': 'user-item', 'type': 'userMessage', 'content': [{'type': 'text', 'text': 'Earlier message'}]},
            {'id': 'answer-item', 'type': 'agentMessage', 'text': 'Earlier answer'}]}
        active = {'id': 'active-turn', 'status': 'inProgress', 'items': [
            {'id': 'active-item', 'type': 'agentMessage', 'text': 'Partial answer'}]}
        for method, turn in [('turn/completed', completed), ('turn/started', active)]:
            fixture.native.event({'method': method, 'params': {'threadId': identifier, 'turn': turn}})
        fixture.native.threads[identifier]['status'] = {'type': 'active'}
        previous = copy.deepcopy(sessions.states[identifier]['turns'])
        state = sessions.refresh(identifier)
        self.assertEqual(state['turns'], previous)
        self.assertEqual(state['threadRuntimeStatus'], {'type': 'active'})

    def test_resumed_metadata_only_thread_keeps_its_saved_history_visible(self):
        fixture = self.fixture()
        identifier = fixture.create('', str(uuid.uuid4()))
        fixture.native.persisted.add(identifier)
        saved = {'id': identifier, 'cwd': fixture.native.threads[identifier]['cwd'],
                 'title': 'Saved conversation', 'modelProvider': 'fixture-provider',
                 'turns': [{'turnId': 'old-turn', 'status': 'completed', 'items': [
                     {'id': 'old-user', 'type': 'userMessage',
                      'content': [{'type': 'text', 'text': 'Saved request'}]},
                     {'id': 'old-answer', 'type': 'agentMessage', 'text': 'Saved answer'}]}]}
        fixture.owner.store.history = lambda identifier, **kwargs: copy.deepcopy(saved)
        fixture.owner.cli_sessions.store = fixture.owner.store
        fixture.owner.ipc.connect = Mock(side_effect=IPCError('desktop absent'))
        sessions = fixture.owner.cli_sessions
        sessions.joined.clear()
        sessions.metadata.clear()
        sessions.states.clear()
        fixture.owner.live.clear()
        original = fixture.native.request

        def request(method, params, **kwargs):
            result = original(method, params, **kwargs)
            if method == 'thread/resume':
                result.update(cwd=saved['cwd'], modelProvider='fixture-provider', model='fixture-model')
            return result

        fixture.native.request = request
        session = fixture.owner.activate(identifier)
        self.assertTrue(session.connected)
        self.assertEqual(sessions.states[identifier]['turns'], [])
        view = fixture.owner.view(identifier, attach=False)
        self.assertEqual([turn['id'] for turn in view['turns']], ['old-turn'])
        self.assertIn('Saved answer', str(fixture.owner.timeline_read(identifier)))
        self.assertEqual(sum(method == 'thread/start' for method, _ in fixture.native.calls), 1)
        self.assertFalse(any(method == 'turn/start' for method, _ in fixture.native.calls))

    def test_other_native_read_errors_never_fall_back_to_metadata_success(self):
        for error in (AppServerUnsupported('another operation is not supported'),
                      AppServerUnknownResult('Synthetic unknown read result'),
                      AppServerUnavailable('Synthetic read disconnected'),
                      AppServerError('Synthetic read rejected')):
            with self.subTest(error=type(error).__name__):
                fixture = self.fixture()
                identifier = fixture.create('', str(uuid.uuid4()))
                before = len(fixture.native.calls)

                def request(method, params, **kwargs):
                    fixture.native.calls.append((method, copy.deepcopy(params)))
                    raise error

                fixture.native.request = request
                with self.assertRaises(type(error)):
                    fixture.owner.cli_sessions.refresh(identifier)
                self.assertEqual(fixture.native.calls[before:], [
                    ('thread/read', {'threadId': identifier, 'includeTurns': True})])

    def test_connection_change_before_or_during_metadata_read_is_rejected(self):
        for during_metadata in (False, True):
            with self.subTest(during_metadata=during_metadata):
                fixture = self.fixture()
                identifier = fixture.create('', str(uuid.uuid4()))
                original = fixture.native.request
                before = len(fixture.native.calls)

                def request(method, params, **kwargs):
                    if params.get('includeTurns') and not during_metadata:
                        fixture.native.process = object()
                    result = original(method, params, **kwargs)
                    if not params.get('includeTurns') and during_metadata:
                        fixture.native.process = object()
                    return result

                fixture.native.request = request
                with self.assertRaisesRegex(AppServerUnavailable, 'connection changed'):
                    fixture.owner.cli_sessions.refresh(identifier)
                reads = fixture.native.calls[before:]
                self.assertEqual(len(reads), 2 if during_metadata else 1)

    def test_metadata_only_refresh_rejects_changed_native_identity_or_unloaded_thread(self):
        for change in ({'id': str(uuid.uuid4())}, {'cwd': '/other/project'},
                       {'modelProvider': 'other-provider'}, {'status': {'type': 'notLoaded'}}):
            with self.subTest(change=change):
                fixture = self.fixture()
                identifier = fixture.create('', str(uuid.uuid4()))
                fixture.native.threads[identifier].update(change)
                with self.assertRaises((AppServerUnsupported, AppServerUnavailable)):
                    fixture.owner.cli_sessions.refresh(identifier)
                self.assertFalse(any(method == 'turn/start' for method, _ in fixture.native.calls))
        fixture = self.fixture()
        identifier = fixture.create('', str(uuid.uuid4()))
        fixture.native.is_loaded = lambda identifier: False
        before = len(fixture.native.calls)
        with self.assertRaisesRegex(AppServerUnavailable, 'unloaded'):
            fixture.owner.cli_sessions.refresh(identifier)
        self.assertEqual(fixture.native.calls[before:], [
            ('thread/read', {'threadId': identifier, 'includeTurns': True})])
        self.assertNotIn(identifier, fixture.owner.cli_sessions.joined)

    def test_current_guard_reads_native_identity_and_installs_latest_event_state(self):
        fixture = self.fixture()
        identifier = fixture.create('', str(uuid.uuid4()))
        original = fixture.native.request
        before = len(fixture.native.calls)

        def request(method, params, **kwargs):
            result = original(method, params, **kwargs)
            fixture.native.event({'method': 'thread/name/updated', 'params': {
                'threadId': identifier, 'threadName': 'Latest native title'}})
            return result

        fixture.native.request = request
        with fixture.owner.cli_sessions.require_current(identifier) as state:
            self.assertEqual(state['threadName'], 'Latest native title')
        self.assertEqual(fixture.native.calls[before:], [
            ('thread/read', {'threadId': identifier, 'includeTurns': False})])

    def test_current_guard_rejects_connection_rejoin_or_identity_change_during_native_read(self):
        for change in ('connection', 'rejoin', 'provider'):
            with self.subTest(change=change):
                fixture = self.fixture()
                identifier = fixture.create('', str(uuid.uuid4()))
                sessions = fixture.owner.cli_sessions
                original = fixture.native.request

                def request(method, params, **kwargs):
                    result = original(method, params, **kwargs)
                    if change == 'connection':
                        fixture.native.process = object()
                    elif change == 'rejoin':
                        sessions.metadata[identifier]['connectionToken'] = str(uuid.uuid4())
                    else:
                        result['thread']['modelProvider'] = 'other-provider'
                    return result

                fixture.native.request = request
                with self.assertRaises(AppServerUnavailable):
                    with sessions.require_current(identifier):
                        self.fail('Changed native evidence cannot permit state installation')

    def test_current_guard_rejects_explicit_native_settings_changes(self):
        for change in ({'model': 'other-model'}, {'reasoningEffort': 'high'}, {'name': 'Other title'}):
            with self.subTest(change=change):
                fixture = self.fixture()
                identifier = fixture.create('', str(uuid.uuid4()))
                fixture.native.threads[identifier].update(change)
                before = len(fixture.native.calls)
                with self.assertRaisesRegex(AppServerUnavailable, 'settings changed'):
                    with fixture.owner.cli_sessions.require_current(identifier):
                        self.fail('Changed native settings cannot permit state installation')
                self.assertEqual(fixture.native.calls[before:], [
                    ('thread/read', {'threadId': identifier, 'includeTurns': False})])
        fixture = self.fixture()
        identifier = fixture.create('Manual title', str(uuid.uuid4()))
        fixture.native.threads[identifier]['name'] = None
        with self.assertRaisesRegex(AppServerUnavailable, 'settings changed'):
            with fixture.owner.cli_sessions.require_current(identifier):
                self.fail('An explicit native title removal must be observed')

    def test_current_guard_preserves_settings_events_newer_than_its_metadata_read(self):
        fixture = self.fixture()
        identifier = fixture.create('', str(uuid.uuid4()))
        original = fixture.native.request

        def request(method, params, **kwargs):
            result = original(method, params, **kwargs)
            fixture.native.event({'method': 'thread/settings/updated', 'params': {
                'threadId': identifier, 'threadSettings': {'model': 'latest-model', 'effort': 'high'}}})
            return result

        fixture.native.request = request
        with fixture.owner.cli_sessions.require_current(identifier) as state:
            self.assertEqual(state['latestModel'], 'latest-model')
            self.assertEqual(state['latestReasoningEffort'], 'high')

    def test_current_guard_does_not_infer_settings_changes_from_absent_metadata(self):
        fixture = self.fixture()
        identifier = fixture.create('Manual title', str(uuid.uuid4()))
        for key in ('model', 'reasoningEffort', 'name'):
            fixture.native.threads[identifier].pop(key, None)
        with fixture.owner.cli_sessions.require_current(identifier) as state:
            self.assertEqual(state['latestModel'], 'fixture-model')
            self.assertEqual(state['threadName'], 'Manual title')

    def test_current_guard_blocks_disconnect_only_during_local_installation(self):
        fixture = self.fixture()
        identifier = fixture.create('', str(uuid.uuid4()))
        started, finished = threading.Event(), threading.Event()

        def disconnect():
            started.set()
            fixture.owner.cli_sessions._disconnect()
            finished.set()

        worker = threading.Thread(target=disconnect)
        try:
            with fixture.owner.cli_sessions.require_current(identifier) as state:
                worker.start()
                self.assertTrue(started.wait(1))
                self.assertFalse(finished.wait(.05))
                self.assertEqual(state['id'], identifier)
            worker.join(1)
            self.assertTrue(finished.is_set())
            self.assertNotIn(identifier, fixture.owner.cli_sessions.joined)
        finally:
            if worker.is_alive():
                worker.join(1)


if __name__ == '__main__':
    unittest.main()
