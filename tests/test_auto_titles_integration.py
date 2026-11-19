"""Model titles across real CLI/WSL workflows, with native I/O replaced."""
import copy
import json
import tempfile
import threading
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from bridge.app.service import Bridge
from bridge.clients.codex.app_server import AppServerUnknownResult
from bridge.clients.codex.ipc import IPCError
from bridge.features.sessions.auto_titles import AutomaticTitles
from bridge.features.endpoints.registry import entry_id
from bridge.features.endpoints.wsl import WslEntry


ROOT = Path(__file__).resolve().parents[1]


class ModelTitleRuntime:
    """A daemon that generates a title only when explicitly given a private turn."""
    def __init__(self, home):
        self.home = home
        self.socket_path = str(home / 'synthetic.sock')
        Path(self.socket_path).touch()
        self.process, self.transport = object(), object()
        self.closed = False
        self.calls, self.responses, self.threads = [], [], {}
        self.lock = threading.RLock()
        self.event = lambda message: None
        self.title_started = threading.Event()
        self.title_written = threading.Event()
        self.title_cleaned = threading.Event()
        self.title_release = threading.Event()
        self.hold_title = False
        self.fail_original = False
        self.fail_name = False
        self.omit_source_model = False
        self.reject_fixed_title_model = False
        self.title_response = json.dumps({'title': 'Duplicate notification diagnosis'})
        self.generated_title = 'Duplicate notification diagnosis'
        self.private_tool_request = False
        self.before_original_ack = None
        self.after_title_name_read = None
        self.workers = []

    def connect(self):
        return {'codexHome': str(self.home)}

    def is_loaded(self, identifier):
        return identifier in self.threads and self.threads[identifier]['status']['type'] != 'notLoaded'

    def request(self, method, params, **options):
        guard = options.get('current_guard')
        if guard is not None and not guard():
            error = IPCError('Synthetic original connection changed before bytes')
            error.not_sent = True
            raise error
        with self.lock:
            self.calls.append((method, copy.deepcopy(params)))
        if method == 'thread/start':
            if params.get('ephemeral') and params.get('model') == 'gpt-6-luna' and self.reject_fixed_title_model:
                error = IPCError('Synthetic provider rejects the fixed naming model')
                error.rpc_error = {'code': -32602, 'message': 'Unsupported model gpt-6-luna'}
                raise error
            identifier = str(uuid.uuid4())
            thread = self.threads[identifier] = {
                'id': identifier, 'cwd': params['cwd'], 'modelProvider': params.get('modelProvider') or 'fixture-provider',
                'model': params.get('model') or 'fixture-model', 'name': None, 'preview': '', 'turns': [],
                'ephemeral': params.get('ephemeral', False), 'status': {'type': 'idle'},
                'canAcceptDirectInput': True}
            snapshot = copy.deepcopy(thread)
            result = {'thread': snapshot,
                      'sandbox': {'type': 'readOnly' if params.get('ephemeral') else 'workspaceWrite'},
                      'activePermissionProfile': {'id': ':read-only' if params.get('ephemeral') else ':workspace'}}
            if self.omit_source_model and not params.get('ephemeral'):
                snapshot.pop('model')
            else:
                result['model'] = thread['model']
            return result
        if method in ('thread/read', 'thread/resume'):
            thread = copy.deepcopy(self.threads[params['threadId']])
            if method == 'thread/read' and thread.get('name') == self.generated_title and self.after_title_name_read:
                callback, self.after_title_name_read = self.after_title_name_read, None
                callback(params['threadId'])
            if method == 'thread/resume' and thread['status']['type'] == 'notLoaded':
                thread['status'] = self.threads[params['threadId']]['status'] = {'type': 'idle'}
            result = {'thread': thread, 'activePermissionProfile': {'id': ':workspace'}}
            if self.omit_source_model and not thread['ephemeral']:
                thread.pop('model')
            else:
                result['model'] = thread['model']
            return result
        if method == 'thread/turns/list':
            turns = copy.deepcopy(self.threads[params['threadId']]['turns'])
            return {'data': list(reversed(turns)), 'nextCursor': None}
        if method == 'config/read':
            config = {'model_provider': 'fixture-provider',
                      'mcp_servers': {'fixture-server': {'enabled': True}}}
            if not self.omit_source_model:
                config['model'] = 'fixture-model'
            return {'config': config}
        if method == 'turn/start':
            thread = self.threads[params['threadId']]
            turn = {'id': str(uuid.uuid4()), 'status': 'inProgress', 'items': [{
                'id': str(uuid.uuid4()), 'type': 'userMessage', 'content': copy.deepcopy(params['input'])}]}
            thread['turns'].append(turn)
            thread['status'] = {'type': 'active'}
            if thread['ephemeral']:
                self.title_started.set()

                def finish():
                    if self.hold_title and not self.title_release.wait(5):
                        return
                    if self.closed:
                        return
                    if self.private_tool_request:
                        self.event({'id': 'private-tool', 'method': 'item/commandExecution/requestApproval',
                                    'params': {'threadId': thread['id'], 'turnId': turn['id'],
                                               'command': 'synthetic command'}})
                    item = {'id': 'private-answer', 'type': 'agentMessage', 'text': self.title_response}
                    turn.update(status='completed', items=[item])
                    thread['status'] = {'type': 'idle'}
                    self.event({'method': 'item/completed', 'params': {
                        'threadId': thread['id'], 'turnId': turn['id'], 'item': copy.deepcopy(item)}})
                    self.event({'method': 'turn/completed', 'params': {
                        'threadId': thread['id'], 'turn': copy.deepcopy(turn)}})

                if self.hold_title:
                    worker = threading.Thread(target=finish, daemon=True)
                    self.workers.append(worker)
                    worker.start()
                else:
                    # Native events can arrive before the turn/start reply.
                    finish()
            else:
                thread['preview'] = params['input'][0]['text']
                self.event({'method': 'turn/started', 'params': {
                    'threadId': thread['id'], 'turn': copy.deepcopy(turn)}})
                self.event({'method': 'item/completed', 'params': {
                    'threadId': thread['id'], 'turnId': turn['id'], 'item': copy.deepcopy(turn['items'][0])}})
                if self.fail_original:
                    raise AppServerUnknownResult('Synthetic first message result unknown')
                if self.before_original_ack is not None:
                    self.before_original_ack(thread['id'])
            return {'turn': copy.deepcopy(turn)}
        if method == 'thread/name/set':
            self.threads[params['threadId']]['name'] = params['name']
            self.event({'method': 'thread/name/updated', 'params': {
                'threadId': params['threadId'], 'threadName': params['name']}})
            if params['name'] == self.generated_title:
                self.title_written.set()
                if self.fail_name:
                    raise AppServerUnknownResult('Synthetic title write result unknown')
            return {}
        if method == 'thread/unsubscribe':
            self.threads[params['threadId']]['status'] = {'type': 'notLoaded'}
            if self.threads[params['threadId']]['ephemeral']:
                self.title_cleaned.set()
            return {'status': 'unsubscribed'}
        if method == 'turn/interrupt':
            return {}
        if method == 'thread/goal/get':
            return {'goal': None}
        if method == 'thread/backgroundTerminals/list':
            return {'data': []}
        raise AssertionError('Unexpected synthetic native operation: ' + method)

    def respond(self, request_id, result=None, error=None, **options):
        guard = options.get('current_guard')
        if guard is not None and not guard():
            raise IPCError('Synthetic response connection changed')
        self.responses.append({'id': request_id, 'result': result, 'error': error})

    def complete_original(self, identifier):
        thread = self.threads[identifier]
        turn = thread['turns'][-1]
        turn['status'] = 'completed'
        thread['status'] = {'type': 'idle'}
        self.event({'method': 'turn/completed', 'params': {
            'threadId': identifier, 'turn': copy.deepcopy(turn)}})

    def close(self):
        self.closed, self.process = True, None
        self.title_release.set()
        for worker in self.workers:
            worker.join(2)


class TitleWorkflowFixture:
    def __init__(self, test, surface):
        self.surface = surface
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        test.addCleanup(temporary.cleanup)
        home = Path(temporary.name)
        self.home = home
        self.native = ModelTitleRuntime(home)
        if surface in ('cli', 'independent', 'ssh'):
            options = ({'host': 'codex:fixture'} if surface == 'independent' else
                       {'host': 'ssh-fixture', 'alias': 'synthetic-target'} if surface == 'ssh' else {})
            with patch.object(Bridge, '_maintain', lambda owner: owner.closed.wait()):
                self.owner = Bridge(home, home / 'data', codex_bin=home / 'no-real-codex', **options)
            self.owner.cli_sessions.client = self.native
            self.owner.cli_sessions._queue_refresh = lambda identifier: None
            self.native.event = self.owner.cli_sessions._event
            self.owner.hosts.state = lambda: {}
            self.owner.hosts.hosts = lambda: {}
            self.owner.ipc.connect = Mock(side_effect=AssertionError('No real desktop client may start'))
            self.project = self.owner.host + '|independent'
        else:
            row = {'id': entry_id('Fixture', 'fixture', str(home)), 'distribution': 'Fixture',
                   'user': 'fixture', 'home': str(home), 'label': 'Fixture WSL'}
            with patch.object(WslEntry, '_maintain', lambda owner: owner.closed.wait()):
                self.owner = WslEntry(row, home / 'data')
            self.owner.server, self.owner.generation = self.native, 'fixture-generation'
            self.native.event = lambda message: self.owner._event(message, 'fixture-generation')
            self.owner._account_route = lambda: {'managed': False}
            self.owner.read = lambda *args, **kwargs: {'cwd': str(home)}
            self.project = self.owner.host + '|independent'
        self.owner.store = SimpleNamespace(home=home, get=self.metadata, list=self.list,
                                          history=self.history)
        test.addCleanup(self.native.close)
        test.addCleanup(self.owner.close)

    def metadata(self, identifier):
        thread = self.native.threads[identifier]
        if thread['ephemeral']:
            raise KeyError('Private title threads are never saved inventory')
        return {'id': identifier, 'origin': 'cli', 'cwd': thread['cwd'],
                'model_provider': thread['modelProvider'], 'name': thread['name'], 'title': thread['preview']}

    def list(self, **options):
        return [self.metadata(identifier) for identifier, thread in self.native.threads.items()
                if not thread['ephemeral']]

    def history(self, identifier, **options):
        thread = self.native.threads[identifier]
        return {'id': identifier, 'cwd': thread['cwd'], 'title': thread['name'] or thread['preview'],
                'turns': copy.deepcopy(thread['turns']), 'historyComplete': True}

    def create(self, title='', request=None):
        request = request or str(uuid.uuid4())
        options = {'kind': 'cli'} if self.surface != 'wsl' else {}
        return self.owner.create_chat(self.project, title, request, **options)['id']

    def wait_title_status(self, test, identifier, expected):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = (self.owner.auto_titles.status(identifier) or {}).get('status')
            if status in expected:
                return status
            if status in ('confirmed', 'failed', 'cancelled', 'unknown'):
                break
            time.sleep(.005)
        row = self.owner.auto_titles.status(identifier) or {}
        test.fail('Automatic title did not reach ' + repr(expected) + ': ' +
                  repr((row.get('status'), row.get('error'))))


class AutomaticTitleIntegrationTests(unittest.TestCase):
    def assert_fixed_naming_model(self, fixture, identifier, main_model='fixture-model'):
        starts = [params for method, params in fixture.native.calls
                  if method == 'thread/start' and params.get('ephemeral')]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]['model'], 'gpt-6-luna')
        self.assertEqual(starts[0]['modelProvider'], fixture.native.threads[identifier]['modelProvider'])
        title_turns = [params for method, params in fixture.native.calls if method == 'turn/start' and
                       fixture.native.threads[params['threadId']]['ephemeral']]
        self.assertEqual(len(title_turns), 1)
        self.assertEqual(title_turns[0]['effort'], 'low')
        self.assertEqual(fixture.native.threads[identifier]['model'], main_model)
        main_turns = [params for method, params in fixture.native.calls
                      if method == 'turn/start' and params['threadId'] == identifier]
        self.assertTrue(main_turns)
        self.assertTrue(all(params.get('model') != 'gpt-6-luna' for params in main_turns))

    def test_later_cli_message_does_not_cancel_the_first_pending_model_title(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                fixture.native.hold_title = True
                identifier = fixture.create()
                fixture.owner.send(identifier, 'First message to name', str(uuid.uuid4()))
                self.assertTrue(fixture.native.title_started.wait(2))
                fixture.native.complete_original(identifier)
                fixture.owner.send(identifier, 'A second message', str(uuid.uuid4()))
                fixture.native.title_release.set()
                self.assertTrue(fixture.native.title_written.wait(2), 'A later message cancelled the first title task')
                starts = [params for method, params in fixture.native.calls if method == 'thread/start']
                self.assertEqual(sum(bool(params.get('ephemeral')) for params in starts), 1)

    def test_lost_first_send_title_receipt_cannot_name_a_later_message(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                identifier = fixture.create()
                receipt = fixture.owner.auto_titles.path
                backup = receipt.with_suffix('.saved')
                receipt.rename(backup)
                receipt.mkdir()
                fixture.native.fail_original = True
                with self.assertRaises((AppServerUnknownResult, IPCError)):
                    fixture.owner.send(identifier, 'First message with unknown result', str(uuid.uuid4()))
                self.assertFalse(fixture.native.title_started.is_set())
                fixture.owner.auto_titles.close()
                receipt.rmdir()
                backup.rename(receipt)
                fixture.owner.auto_titles = AutomaticTitles(receipt.parent, fixture.owner._run_auto_title)
                fixture.native.fail_original = False
                fixture.native.complete_original(identifier)
                fixture.owner.send(identifier, 'A later distinct message', str(uuid.uuid4()))
                fixture.wait_title_status(self, identifier, {'cancelled'})
                self.assertFalse(fixture.native.title_started.is_set())

    def test_first_attachment_message_generates_title_without_copying_file_content(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                identifier = fixture.create()
                file = {'name': 'diagram.png', 'path': str(fixture.native.home / 'diagram.png'), 'image': True}
                fixture.owner.uploads.resolve = lambda thread_id, identifiers: [file] if identifiers else []
                result = fixture.owner.send(identifier, '  ', str(uuid.uuid4()), attachments=['synthetic-upload'])
                self.assertEqual(result['status'], 'accepted')
                self.assertTrue(fixture.native.title_written.wait(2), 'Attachment-only first message was never named')
                title_turn = next(params for method, params in fixture.native.calls
                                  if method == 'turn/start' and fixture.native.threads[params['threadId']]['ephemeral'])
                self.assertEqual([item['type'] for item in title_turn['input']], ['text'])
                self.assertIn(file['name'], title_turn['input'][0]['text'])
                self.assertNotIn(file['path'], title_turn['input'][0]['text'])

    def test_accepted_first_message_generates_title_while_main_reply_is_running(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                creation, submission = str(uuid.uuid4()), str(uuid.uuid4())
                identifier = fixture.create('', creation)
                prompt = 'Find why notifications are duplicated and fix the server.'
                self.assertEqual(fixture.owner.send(identifier, prompt, submission)['status'], 'accepted')
                self.assertTrue(fixture.native.title_written.wait(2), 'No model-generated title was saved')
                self.assert_fixed_naming_model(fixture, identifier)
                state = fixture.native.threads[identifier]
                self.assertEqual(state['name'], fixture.native.generated_title)
                self.assertEqual(state['status'], {'type': 'active'})
                self.assertEqual(state['turns'][0]['items'][0]['content'][0]['text'], prompt)
                self.assertEqual(len(state['turns']), 1)
                starts = [params for method, params in fixture.native.calls if method == 'thread/start']
                self.assertEqual(sum(not params.get('ephemeral') for params in starts), 1)
                self.assertEqual(sum(bool(params.get('ephemeral')) for params in starts), 1)
                self.assertEqual(set(fixture.owner.live), {identifier})
                self.assertEqual(fixture.create(None, creation), identifier)
                self.assertEqual(fixture.owner.send(identifier, prompt, submission)['status'], 'accepted')
                self.assertEqual(sum(method == 'turn/start' for method, _ in fixture.native.calls), 2)

    def test_creation_and_passive_reads_do_not_start_model_naming(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                identifier = fixture.create()
                if surface == 'cli':
                    fixture.owner.cli_sessions.refresh(identifier)
                else:
                    fixture.owner._refresh(fixture.owner.live[identifier])
                self.assertEqual(fixture.owner.live[identifier].view()['title'], '未命名聊天')
                self.assertFalse(fixture.native.title_started.is_set())
                self.assertFalse(any(method == 'turn/start' for method, _ in fixture.native.calls))

    def test_manual_creation_name_does_not_generate_model_title(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                identifier = fixture.create('My chosen title')
                fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
                self.assertEqual(fixture.native.threads[identifier]['name'], 'My chosen title')
                self.assertFalse(fixture.native.title_started.is_set())
                self.assertEqual(sum(method == 'turn/start' for method, _ in fixture.native.calls), 1)

    def test_queued_first_message_names_only_after_actual_dispatch(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                identifier, submission = fixture.create(), str(uuid.uuid4())
                self.assertEqual(fixture.owner.send(identifier, 'Queued first message', submission, mode='queue')['status'], 'queued')
                self.assertFalse(fixture.native.title_started.is_set())
                self.assertFalse(any(method == 'turn/start' for method, _ in fixture.native.calls))
                if surface == 'cli':
                    key = identifier + ':' + submission
                    fixture.owner._send_queued(fixture.owner.live[identifier], key, fixture.owner.submissions[key])
                else:
                    fixture.owner._dispatch_queued(identifier)
                self.assertTrue(fixture.native.title_written.wait(2))
                self.assertEqual(sum(method == 'turn/start' for method, _ in fixture.native.calls), 2)

    def test_manual_rename_cancels_model_title_before_it_can_overwrite(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                fixture.native.hold_title = True
                identifier = fixture.create()
                fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
                self.assertTrue(fixture.native.title_started.wait(2))

                if surface == 'cli':
                    # Plain CLI rename is performed by its original native client.
                    fixture.native.request('thread/name/set', {'threadId': identifier, 'name': 'My manual title'})
                else:
                    fixture.owner.rename(identifier, 'My manual title')
                fixture.native.title_release.set()
                fixture.wait_title_status(self, identifier, {'cancelled'})
                self.assertEqual(fixture.native.threads[identifier]['name'], 'My manual title')
                self.assertFalse(fixture.native.title_written.is_set())

    def test_invalid_model_result_does_not_change_accepted_message(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                fixture.native.title_response = 'invalid structured result'
                identifier, submission = fixture.create(), str(uuid.uuid4())
                self.assertEqual(fixture.owner.send(identifier, 'First message', submission)['status'], 'accepted')
                fixture.wait_title_status(self, identifier, {'failed'})
                self.assertIsNone(fixture.native.threads[identifier]['name'])
                self.assertEqual(fixture.owner.send(identifier, 'First message', submission)['status'], 'accepted')
                self.assertEqual(sum(method == 'turn/start' for method, _ in fixture.native.calls), 2)

    def test_private_title_requests_are_denied_and_never_displayed_as_approvals(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                fixture.native.private_tool_request = True
                identifier = fixture.create()
                fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
                fixture.wait_title_status(self, identifier, {'failed'})
                self.assertEqual(set(fixture.owner.live), {identifier})
                self.assertEqual(fixture.owner.live[identifier].view()['requests'], [])
                self.assertEqual([response['id'] for response in fixture.native.responses], ['private-tool'])
                self.assertEqual(fixture.native.responses[0]['error']['code'], -32000)
                self.assertIsNone(fixture.native.threads[identifier]['name'])

    def test_unknown_first_submission_is_not_replayed_or_named_by_a_later_message(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                fixture.native.fail_original = True
                identifier, submission = fixture.create(), str(uuid.uuid4())
                with self.assertRaises((IPCError, AppServerUnknownResult)):
                    fixture.owner.send(identifier, 'Unknown first message', submission)
                fixture.native.fail_original = False
                self.assertEqual(fixture.owner.send(identifier, 'Unknown first message', submission)['status'], 'unknown')
                fixture.native.complete_original(identifier)
                self.assertEqual(fixture.owner.send(identifier, 'Later message', str(uuid.uuid4()))['status'], 'accepted')
                self.assertFalse(fixture.native.title_started.is_set())
                self.assertEqual(sum(method == 'turn/start' for method, _ in fixture.native.calls), 2)
                self.assertIsNone(fixture.native.threads[identifier]['name'])

    def test_unknown_title_write_is_not_replayed_after_duplicate_send(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                fixture.native.fail_name = True
                identifier, submission = fixture.create(), str(uuid.uuid4())
                self.assertEqual(fixture.owner.send(identifier, 'First message', submission)['status'], 'accepted')
                fixture.wait_title_status(self, identifier, {'unknown'})
                fixture.native.fail_name = False
                self.assertEqual(fixture.owner.send(identifier, 'First message', submission)['status'], 'accepted')
                self.assertEqual(sum(method == 'thread/name/set' for method, _ in fixture.native.calls), 1)

    def test_wsl_release_cancels_title_and_only_cleans_up_its_original_temporary_thread(self):
        fixture = TitleWorkflowFixture(self, 'wsl')
        fixture.native.hold_title = True
        identifier = fixture.create()
        fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
        self.assertTrue(fixture.native.title_started.wait(2))
        fixture.native.complete_original(identifier)
        token = fixture.owner.subscription_tokens[identifier]
        self.assertIn(fixture.owner.release_connection(identifier, token)['status'], ('released', 'detached'))
        fixture.native.title_release.set()
        fixture.wait_title_status(self, identifier, {'cancelled'})
        self.assertFalse(fixture.native.title_written.is_set())
        self.assertFalse(any(method == 'thread/name/set' for method, _ in fixture.native.calls))

    def test_independent_entry_generates_with_its_own_registered_home(self):
        fixture = TitleWorkflowFixture(self, 'independent')
        identifier = fixture.create()
        fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
        self.assertTrue(fixture.native.title_written.wait(2))
        self.assert_fixed_naming_model(fixture, identifier)
        identity = fixture.owner.auto_titles.status(identifier)['identity']
        self.assertEqual(identity['host'], 'codex:fixture')
        self.assertEqual(identity['home'], str(fixture.home))
        self.assertEqual(identity['modelProvider'], 'fixture-provider')
        self.assertEqual(fixture.native.threads[identifier]['cwd'], identity['cwd'])

    def test_ssh_creation_enrolls_on_execution_bridge_and_preserves_remote_route(self):
        router = TitleWorkflowFixture(self, 'cli')
        target = TitleWorkflowFixture(self, 'ssh')
        host, alias = target.owner.host, target.owner.cli_sessions.alias
        project = 'cli|' + host + '|' + 'a' * 20
        hosts = {host: {'alias': alias}}
        router.owner.hosts.hosts = lambda: hosts
        target.owner.hosts.hosts = lambda: hosts
        router.owner.for_host = lambda value: target.owner if value == host else router.owner
        router.owner.project_host = lambda value: host
        router.owner.projects = lambda **options: [
            {'key': project, 'host': host, 'cwd': str(target.home), 'type': 'cli'}]
        identifier = router.owner.create_chat(project, '', str(uuid.uuid4()), kind='cli')['id']
        self.assertIsNone(router.owner.auto_titles.status(identifier))
        target.owner.send(identifier, 'Remote first message', str(uuid.uuid4()))
        self.assertTrue(target.native.title_written.wait(2))
        self.assert_fixed_naming_model(target, identifier)
        identity = target.owner.auto_titles.status(identifier)['identity']
        self.assertEqual(identity['host'], host)
        self.assertEqual(identity['home'], str(target.home))
        self.assertEqual(identity['cwd'], str(target.home))
        self.assertFalse(any(method == 'thread/start' for method, _ in router.native.calls))

    def test_wsl_generation_change_during_model_work_prevents_name_write(self):
        fixture = TitleWorkflowFixture(self, 'wsl')
        fixture.native.hold_title = True
        identifier = fixture.create()
        fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
        self.assertTrue(fixture.native.title_started.wait(2))
        fixture.owner.generation = 'replacement-generation'
        fixture.native.title_release.set()
        fixture.wait_title_status(self, identifier, {'cancelled'})
        self.assertFalse(any(method == 'thread/name/set' for method, _ in fixture.native.calls))
        self.assertIsNone(fixture.native.threads[identifier]['name'])

    def test_wsl_authoritative_identity_change_prevents_generated_name_write(self):
        for field, replacement in (('cwd', '/different/project'), ('modelProvider', 'other-provider')):
            with self.subTest(field=field):
                fixture = TitleWorkflowFixture(self, 'wsl')
                fixture.native.hold_title = True
                identifier = fixture.create()
                fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
                self.assertTrue(fixture.native.title_started.wait(2))
                fixture.native.threads[identifier][field] = replacement
                fixture.native.title_release.set()
                fixture.wait_title_status(self, identifier, {'cancelled', 'failed'})
                self.assertFalse(any(method == 'thread/name/set' for method, _ in fixture.native.calls))

    def test_recovery_created_blank_chat_waits_for_its_explicit_first_send(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                source = fixture.create('Existing source title')
                result = fixture.owner.recover(source, {'id': str(uuid.uuid4())})
                identifier = result['id']
                self.assertTrue(result['ready'])
                self.assertNotEqual(identifier, source)
                self.assertEqual(fixture.owner.auto_titles.status(identifier)['status'], 'armed')
                self.assertFalse(any(method == 'turn/start' for method, _ in fixture.native.calls))
                fixture.owner.send(identifier, 'First message in the new chat', str(uuid.uuid4()))
                self.assertTrue(fixture.native.title_written.wait(2))
                self.assertEqual(fixture.native.threads[source]['name'], 'Existing source title')

    def test_connection_replaced_before_first_send_ack_cannot_receive_title_work(self):
        for surface, changes in (('cli', ('process', 'transport', 'token')), ('wsl', ('generation', 'token'))):
            for change in changes:
                with self.subTest(surface=surface, change=change):
                    fixture = TitleWorkflowFixture(self, surface)
                    identifier = fixture.create()

                    def replace_binding(thread_id):
                        if change == 'process':
                            fixture.native.process = object()
                        elif change == 'transport':
                            fixture.native.transport = object()
                        elif surface == 'cli':
                            fixture.owner.cli_sessions.metadata[thread_id]['connectionToken'] = str(uuid.uuid4())
                        elif change == 'generation':
                            fixture.owner.generation = 'new-generation'
                            fixture.owner.subscriptions[thread_id] = 'new-generation'
                        else:
                            fixture.owner.subscription_tokens[thread_id] = str(uuid.uuid4())

                    fixture.native.before_original_ack = replace_binding
                    self.assertEqual(fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))['status'], 'accepted')
                    fixture.wait_title_status(self, identifier, {'cancelled'})
                    self.assertFalse(fixture.native.title_started.is_set())
                    self.assertEqual(sum(method == 'thread/start' for method, _ in fixture.native.calls), 1)

    def test_new_native_name_event_survives_stale_auto_name_confirmation(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                identifier = fixture.create()
                newer_name = 'Newer name from original client'

                def newer_native_name(thread_id):
                    fixture.native.threads[thread_id]['name'] = newer_name
                    fixture.native.event({'method': 'thread/name/updated', 'params': {
                        'threadId': thread_id, 'threadName': newer_name}})

                fixture.native.after_title_name_read = newer_native_name
                fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
                fixture.wait_title_status(self, identifier, {'cancelled', 'unknown'})
                self.assertEqual(fixture.native.threads[identifier]['name'], newer_name)
                self.assertEqual(fixture.owner.live[identifier].view()['title'], newer_name)
                self.assertEqual(sum(method == 'thread/name/set' for method, _ in fixture.native.calls), 1)

    def test_suspending_cli_observation_cancels_and_cleans_only_private_title_work(self):
        fixture = TitleWorkflowFixture(self, 'cli')
        fixture.native.hold_title = True
        self.addCleanup(fixture.native.title_release.set)
        identifier = fixture.create()
        fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
        self.assertTrue(fixture.native.title_started.wait(2))
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            job = fixture.owner.auto_titles.status(identifier) or {}
            if job.get('tempTurnId'):
                break
            time.sleep(.005)
        self.assertTrue(job.get('tempTurnId'), 'Private model turn was not acknowledged')
        source_before = copy.deepcopy(fixture.native.threads[identifier])
        fixture.owner.observation_enabled = lambda: False
        fixture.owner.suspend_observation()
        fixture.wait_title_status(self, identifier, {'cancelled'})
        self.assertTrue(fixture.native.title_cleaned.wait(2), 'Disabled observation prevented private cleanup')
        cleanup = [(method, params) for method, params in fixture.native.calls
                   if method in ('turn/interrupt', 'thread/unsubscribe')]
        self.assertEqual([method for method, _ in cleanup], ['turn/interrupt', 'thread/unsubscribe'])
        self.assertEqual({params['threadId'] for _, params in cleanup}, {job['tempThreadId']})
        self.assertEqual(cleanup[0][1]['turnId'], job['tempTurnId'])
        self.assertEqual(fixture.native.threads[identifier], source_before)
        self.assertEqual(fixture.owner.live[identifier].view()['status'], 'active')
        self.assertFalse(fixture.native.title_written.is_set())

    def test_delayed_native_name_publication_survives_stale_final_title_patch(self):
        fixture = TitleWorkflowFixture(self, 'cli')
        identifier = fixture.create()
        newer_name = 'Native name published after confirmation'
        publication_entered, publish = threading.Event(), threading.Event()
        self.addCleanup(publish.set)
        published, errors = [], []
        on_state = fixture.owner.cli_sessions.on_state

        def delayed_publication(thread_id, snapshot):
            if snapshot.get('threadName') == newer_name and not publication_entered.is_set():
                publication_entered.set()
                if not publish.wait(3):
                    errors.append('Native name publication was not released')
                    return
            on_state(thread_id, snapshot)

        fixture.owner.cli_sessions.on_state = delayed_publication

        def before_confirmation_returns(thread_id):
            fixture.native.threads[thread_id]['name'] = newer_name
            worker = threading.Thread(target=lambda: fixture.native.event({
                'method': 'thread/name/updated', 'params': {
                    'threadId': thread_id, 'threadName': newer_name}}), daemon=True)
            published.append(worker)
            fixture.native.workers.append(worker)
            worker.start()
            if not publication_entered.wait(2):
                errors.append('Native name event never reached publication')

        fixture.native.after_title_name_read = before_confirmation_returns
        fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
        self.assertTrue(publication_entered.wait(2))
        # Wait for the old confirmation to finish while the newer event's
        # public snapshot is deliberately held at the production callback.
        fixture.wait_title_status(self, identifier, {'cancelled', 'unknown', 'confirmed', 'failed'})
        publish.set()
        for worker in published:
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        fixture.native.complete_original(identifier)
        self.assertEqual(fixture.owner.live[identifier].view()['title'], newer_name)

    def test_fixed_title_model_does_not_require_main_model_metadata(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                # The daemon keeps its main default; native replies expose no
                # main model in start/read/resume or effective config metadata.
                fixture.native.omit_source_model = True
                identifier = fixture.create()
                self.assertEqual(fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))['status'], 'accepted')
                fixture.wait_title_status(self, identifier, {'confirmed'})
                self.assertTrue(fixture.native.title_written.is_set())
                self.assert_fixed_naming_model(fixture, identifier)

    def test_changing_main_model_during_fixed_title_work_does_not_cancel_naming(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                fixture.native.hold_title = True
                identifier = fixture.create()
                fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
                self.assertTrue(fixture.native.title_started.wait(2))
                changed_model = 'changed-main-model'
                fixture.native.threads[identifier]['model'] = changed_model
                fixture.native.event({'method': 'thread/settings/updated', 'params': {
                    'threadId': identifier, 'threadSettings': {'model': changed_model}}})
                fixture.native.title_release.set()
                fixture.wait_title_status(self, identifier, {'confirmed'})
                self.assertTrue(fixture.native.title_written.is_set())
                self.assert_fixed_naming_model(fixture, identifier, main_model=changed_model)

    def test_explicit_fixed_model_rejection_never_falls_back_or_changes_main_send(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = TitleWorkflowFixture(self, surface)
                fixture.native.reject_fixed_title_model = True
                identifier, submission = fixture.create(), str(uuid.uuid4())
                self.assertEqual(fixture.owner.send(identifier, 'First message', submission)['status'], 'accepted')
                fixture.wait_title_status(self, identifier, {'failed'})
                starts = [params for method, params in fixture.native.calls
                          if method == 'thread/start' and params.get('ephemeral')]
                self.assertEqual([params['model'] for params in starts], ['gpt-6-luna'])
                self.assertEqual(starts[0]['modelProvider'], 'fixture-provider')
                self.assertEqual(sum(method == 'turn/start' for method, _ in fixture.native.calls), 1)
                self.assertFalse(fixture.native.title_started.is_set())
                self.assertIsNone(fixture.native.threads[identifier]['name'])
                self.assertEqual(fixture.native.threads[identifier]['model'], 'fixture-model')
                self.assertEqual(fixture.owner.send(identifier, 'First message', submission)['status'], 'accepted')
                self.assertEqual(sum(method == 'thread/start' and bool(params.get('ephemeral'))
                                     for method, params in fixture.native.calls), 1)


if __name__ == '__main__':
    unittest.main()
