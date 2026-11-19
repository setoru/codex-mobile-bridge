"""Native chat titles through the real gateway paths and synthetic runtimes."""
import copy
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bridge.app.service import Bridge
from bridge.clients.codex.app_server import AppServerUnknownResult, AppServerUnsupported
from bridge.clients.codex.ipc import IPCError
from bridge.features.endpoints.registry import entry_id
from bridge.features.endpoints.wsl import WslEntry


ROOT = Path(__file__).resolve().parents[1]


class NativeRuntime:
    """Legacy native I/O for title synchronization, without structured naming config."""
    def __init__(self, home):
        self.home = home
        self.socket_path = str(home / 'fixture.sock')
        Path(self.socket_path).touch()
        self.process, self.closed = object(), False
        self.calls, self.threads = [], {}
        self.fail_send = False
        self.event = lambda value: None

    def connect(self):
        return {'codexHome': str(self.home)}

    def is_loaded(self, identifier):
        return identifier in self.threads

    def request(self, method, params, **kwargs):
        self.calls.append((method, copy.deepcopy(params)))
        if method == 'thread/start':
            identifier = str(uuid.uuid4())
            thread = self.threads[identifier] = {
                'id': identifier, 'cwd': params['cwd'], 'modelProvider': 'fixture-provider',
                'model': 'fixture-model', 'name': None, 'preview': '', 'turns': [],
                'status': {'type': 'idle'}, 'canAcceptDirectInput': True}
            return {'thread': copy.deepcopy(thread), 'model': thread['model']}
        if method in ('thread/read', 'thread/resume'):
            return {'thread': copy.deepcopy(self.threads[params['threadId']])}
        if method == 'thread/turns/list':
            return {'data': list(reversed(copy.deepcopy(self.threads[params['threadId']]['turns']))), 'nextCursor': None}
        if method == 'turn/start':
            if self.fail_send:
                raise AppServerUnknownResult('Synthetic unknown send result')
            thread = self.threads[params['threadId']]
            turn = {'id': str(uuid.uuid4()), 'status': 'completed', 'items': [{
                'id': str(uuid.uuid4()), 'type': 'userMessage', 'content': copy.deepcopy(params['input'])}]}
            thread['turns'].append(turn)
            thread['preview'] = params['input'][0]['text']
            return {'turn': copy.deepcopy(turn)}
        if method == 'thread/name/set':
            self.threads[params['threadId']]['name'] = params['name']
            return {}
        if method == 'config/read':
            raise AppServerUnsupported('Synthetic legacy runtime has no effective configuration interface')
        if method == 'thread/goal/get':
            return {'goal': None}
        raise AssertionError('Unexpected native operation: ' + method)

    def close(self):
        self.closed, self.process = True, None


class ChatFixture:
    def __init__(self, test, surface):
        self.surface = surface
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        test.addCleanup(temporary.cleanup)
        home = Path(temporary.name)
        self.native = NativeRuntime(home)
        if surface == 'cli':
            self.owner = Bridge(home, home / 'data', codex_bin=home / 'no-real-codex')
            self.owner.cli_sessions.client = self.native
            self.native.event = self.owner.cli_sessions._event
            self.owner.hosts.state = lambda: {}
            self.owner.hosts.hosts = lambda: {}
            self.owner.ipc.connect = Mock(side_effect=AssertionError('No desktop client may be started'))
            self.project = 'local|independent'
        else:
            row = {'id': entry_id('Fixture', 'fixture', '/fixture/.codex'), 'distribution': 'Fixture',
                   'user': 'fixture', 'home': '/fixture/.codex', 'label': 'Fixture WSL'}
            self.owner = WslEntry(row, home / 'data')
            self.owner.server, self.owner.generation = self.native, 'fixture-generation'
            self.native.event = lambda message: self.owner._event(message, self.owner.generation)
            self.owner._account_route = lambda: {'managed': False}
            self.owner.read = lambda *args, **kwargs: {'cwd': str(home)}
            self.project = self.owner.host + '|independent'
        self.owner.store = SimpleNamespace(get=self.metadata, list=lambda **kwargs: [], history=self.history)
        test.addCleanup(self.owner.close)

    def metadata(self, identifier):
        thread = self.native.threads[identifier]
        return {'id': identifier, 'origin': 'cli', 'cwd': thread['cwd'],
                'model_provider': thread['modelProvider'], 'name': thread['name'], 'title': thread['preview']}

    def history(self, identifier, **kwargs):
        thread = self.native.threads[identifier]
        return {'id': identifier, 'cwd': thread['cwd'], 'title': thread['name'] or thread['preview'],
                'turns': [], 'historyComplete': True}

    def create(self, title, request):
        options = {'kind': 'cli'} if self.surface == 'cli' else {}
        return self.owner.create_chat(self.project, title, request, **options)['id']

    def refresh(self, identifier):
        if self.surface == 'cli':
            self.owner.cli_sessions.refresh(identifier)
        else:
            self.owner._refresh(self.owner.session(identifier, attach=False))
        return self.owner.live[identifier].view()


class NativeChatTitleTests(unittest.TestCase):
    def test_legacy_runtime_keeps_first_send_and_synchronizes_native_titles(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = ChatFixture(self, surface)
                request, submission = str(uuid.uuid4()), str(uuid.uuid4())
                identifier = fixture.create(None, request)
                for title in ('', '   '):
                    self.assertEqual(fixture.create(title, request), identifier)
                self.assertEqual(fixture.refresh(identifier)['title'], '未命名聊天')
                text = 'Explain this project'
                for _ in range(2):
                    self.assertEqual(fixture.owner.send(identifier, text, submission)['status'], 'accepted')
                self.assertEqual(fixture.refresh(identifier)['title'], text)
                fixture.native.threads[identifier]['name'] = 'Native title'
                fixture.native.event({'method': 'thread/name/updated', 'params': {
                    'threadId': identifier, 'threadName': 'Native title'}})
                self.assertEqual(fixture.owner.live[identifier].view()['title'], 'Native title')
                methods = [method for method, _ in fixture.native.calls]
                self.assertEqual(methods.count('thread/start'), 1)
                self.assertEqual(methods.count('turn/start'), 1)
                self.assertNotIn('thread/name/set', methods)
                self.assertEqual(set(fixture.native.threads), {identifier})
                with self.assertRaisesRegex(ValueError, '不同内容'):
                    fixture.create('Different name', request)

    def test_manual_creation_name_survives_native_preview_updates(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = ChatFixture(self, surface)
                request = str(uuid.uuid4())
                identifier = fixture.create('  My task  ', request)
                self.assertEqual(fixture.create('My task', request), identifier)
                fixture.owner.send(identifier, 'First message', str(uuid.uuid4()))
                self.assertEqual(fixture.refresh(identifier)['title'], 'My task')
                names = [params for method, params in fixture.native.calls if method == 'thread/name/set']
                self.assertEqual(names, [{'threadId': identifier, 'name': 'My task'}])
                self.assertEqual(len(fixture.native.threads), 1)

    def test_unknown_first_send_is_not_replayed_or_named(self):
        for surface in ('cli', 'wsl'):
            with self.subTest(surface=surface):
                fixture = ChatFixture(self, surface)
                identifier = fixture.create('', str(uuid.uuid4()))
                submission = str(uuid.uuid4())
                fixture.native.fail_send = True
                with self.assertRaises((IPCError, AppServerUnknownResult)):
                    fixture.owner.send(identifier, 'First message', submission)
                fixture.native.fail_send = False
                self.assertEqual(fixture.owner.send(identifier, 'First message', submission)['status'], 'unknown')
                methods = [method for method, _ in fixture.native.calls]
                self.assertEqual(methods.count('thread/start'), 1)
                self.assertEqual(methods.count('turn/start'), 1)
                self.assertNotIn('thread/name/set', methods)


if __name__ == '__main__':
    unittest.main()
