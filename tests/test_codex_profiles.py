"""Independent home identities share the existing native Codex session flow."""
import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import Mock, patch

from bridge.app.service import Bridge
from bridge.clients.codex.ipc import IPCError
from bridge.features.endpoints.registry import Entries, local_entry_id
from tests.test_cli_bridge_integration import CliBridgeFixture

ROOT = Path(__file__).resolve().parents[1]


class ProfileRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT/'.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.default = self.home('.codex')
        self.other = self.home('.codex-vscode')
        self.factory = Mock()
        self.registry = Entries(self.root/'bridge', self.default, self.factory)
        self.addCleanup(self.registry.close)

    def home(self, name):
        path = self.root/name; path.mkdir(); (path/'config.toml').write_text('model="test"\n')
        return path

    def test_scan_and_save_are_read_only_and_duplicate_aliases_are_rejected(self):
        result = self.registry.local_profiles(scan=True)
        self.assertIn(str(self.other), [r['home'] for r in result['candidates']])
        self.assertNotIn(str(self.default), [r['home'] for r in result['candidates']])
        with self.assertRaisesRegex(ValueError, '默认'): self.registry.save_local(str(self.default), 'Duplicate')
        alias = self.root/'alias'; alias.symlink_to(self.other, target_is_directory=True)
        result = self.registry.save_local(str(alias), 'IDE')
        self.assertEqual(result['entry'], local_entry_id(self.other))
        with self.assertRaisesRegex(ValueError, '已登记'): self.registry.save_local(str(self.other), 'Duplicate')
        self.factory.assert_not_called()
        again = Entries(self.root/'bridge', self.default, self.factory)
        self.addCleanup(again.close)
        self.assertEqual(again.hosts(), {result['entry']: {'label': 'IDE', 'kind': 'codex-local'}})
        self.assertTrue(again.public()['entries'][0]['enabled'])

    def test_replaced_directory_cannot_redirect_an_existing_entry(self):
        entry = self.registry.save_local(str(self.other), 'IDE')['entry']
        self.other.rename(self.root/'old-home'); self.home('.codex-vscode')
        with self.assertRaisesRegex(ValueError, '身份已变化'): self.registry.get(entry)
        self.factory.assert_not_called()

    def test_external_sqlite_directory_is_not_silently_read_from_another_profile(self):
        (self.other/'config.toml').write_text('sqlite_home='+json.dumps(str(self.default)))
        with self.assertRaisesRegex(ValueError, 'sqlite_home'): self.registry.save_local(str(self.other), 'IDE')


class RuntimeEnvironmentTests(unittest.TestCase):
    def test_helpers_preserve_default_database_and_isolate_extra_home(self):
        import os
        from unittest.mock import patch
        from bridge.features.sessions.create import _runtime_operation, CreationUnavailable
        from bridge.features.sessions.goal import GoalError
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp') as directory, \
                patch.dict(os.environ, {'CODEX_SQLITE_HOME': '/default/sqlite'}):
            root = Path(directory)
            for host in ('local', 'codex:'+'1'*32):
                home = root / host.replace(':', '-')
                home.mkdir()
                bridge = Bridge(home, home/'data', host=host, codex_bin=__file__)
                try:
                    with self.subTest(host=host), patch('subprocess.Popen', side_effect=OSError('probe')) as popen:
                        def check():
                            env = popen.call_args.kwargs['env']
                            self.assertEqual(env['CODEX_HOME'], str(home))
                            self.assertEqual(env.get('CODEX_SQLITE_HOME'), '/default/sqlite' if host=='local' else None)
                            popen.reset_mock()
                        with self.assertRaises(GoalError):
                            bridge.goal.start()
                        check()
                        with self.assertRaises(OSError):
                            bridge.side_chats.factory(__file__, home, 'parent', str(home), {'model':'fixture', 'modelProvider':'fixture'})
                        check()
                        with self.assertRaises(CreationUnavailable):
                            _runtime_operation(__file__, home, home, lambda request: None, isolated_home=host.startswith('codex:'))
                        check()
                finally:
                    bridge.close()


class DefaultIdeTests(CliBridgeFixture):
    def setUp(self):
        super().setUp()
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute("UPDATE threads SET title='Saved IDE', source='vscode', originator='codex_vscode'")
        absent = IPCError('desktop absent')
        absent.__cause__ = FileNotFoundError()
        self.bridge.ipc.connect.side_effect = absent

    def test_mixed_inventory_filters_and_paginates_without_activating(self):
        expected = {'ide': {self.thread_id}, 'desktop': set(), 'cli': {'cli'}}
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            for origin, originator, start in [('desktop', 'Codex Desktop', 200), ('ide', 'codex_vscode', 5)]:
                rows = [(f'{origin}-{i:03}', origin, '/fixture', start + i // 2, 0, originator, 'vscode')
                        for i in range(120)]
                db.executemany('INSERT INTO threads(id,title,cwd,updated_at,archived,originator,source) VALUES(?,?,?,?,?,?,?)', rows)
                expected[origin].update(row[0] for row in rows)
            db.execute("INSERT INTO threads(id,title,cwd,updated_at,archived,originator,source) "
                       "VALUES('cli','CLI','/fixture',2,0,'codex_cli_rs','cli')")
            db.execute("INSERT INTO threads(id,title,cwd,updated_at,archived,originator,source) "
                       "VALUES('archived-ide','Saved IDE','/fixture',999,1,'codex_vscode','vscode')")
        expected['all'] = set.union(*expected.values())
        with patch('subprocess.Popen', side_effect=AssertionError('List launched a process')):
            for local_only in (False, True):
                for source, identifiers in expected.items():
                    with self.subTest(local_only=local_only, source=source):
                        rows, available = self.bridge.list(source=source, limit=1000,
                                                           local_only=local_only, include_sources=True)
                        self.assertEqual({row['id'] for row in rows}, identifiers)
                        self.assertEqual(available, ['cli', 'desktop', 'ide'])
                        if source != 'all':
                            self.assertTrue(all(row['access']['source'] == source for row in rows))
            self.bridge.session_pins.set(self.thread_id, True)
            for pin_order in (False, True):
                with self.subTest(pin_order=pin_order):
                    full = self.bridge.list(limit=1000, pin_order=pin_order)
                    identifiers = [row['id'] for row in full]
                    self.assertEqual(len(identifiers), 242)
                    pages = [row['id'] for offset in (0, 100, 200)
                             for row in self.bridge.list(offset=offset, pin_order=pin_order)]
                    self.assertEqual(pages, identifiers)
                    self.assertEqual(len(set(pages)), len(pages))
                    for limit in (100, 200, 300):
                        prefix = self.bridge.list(limit=limit, pin_order=pin_order)
                        self.assertEqual([row['id'] for row in prefix], identifiers[:limit])
                    self.assertEqual(identifiers[0] == self.thread_id, pin_order)
            self.assertEqual([row['id'] for row in self.bridge.list(query='Saved IDE')], [self.thread_id])
            self.assertEqual([row['id'] for row in self.bridge.list(archived=True, source='ide')], ['archived-ide'])
            self.assertEqual(self.bridge.list(query='not found', include_sources=True), ([], ['cli', 'desktop', 'ide']))
        self.cli.attach.assert_not_called()
        self.cli.call.assert_not_called()
        self.bridge.ipc.connect.assert_not_called()

    def test_visible_ide_parent_groups_attachments_but_preserves_roots(self):
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('ALTER TABLE threads ADD COLUMN attached_thread_parent_id TEXT')
            db.execute('ALTER TABLE threads ADD COLUMN forked_from_id TEXT')
            db.execute('ALTER TABLE threads ADD COLUMN thread_source TEXT')
            db.executemany('INSERT INTO threads(id,title,cwd,updated_at,archived,originator,source,'
                           'attached_thread_parent_id,forked_from_id,thread_source) VALUES(?,?,?,?,?,?,?,?,?,?)', [
                ('attached-app', 'Side', '/fixture', 10, 0, 'Codex Desktop', 'vscode', self.thread_id, None, None),
                ('attached-ide', 'Side', '/fixture', 9, 0, 'codex_vscode', 'vscode', self.thread_id, None, None),
                ('orphan-ide', 'Orphan', '/fixture', 8, 0, 'codex_vscode', 'vscode', 'missing', None, None),
                ('fork-ide', 'Fork', '/fixture', 7, 0, 'codex_vscode', 'vscode', None, self.thread_id, None),
                ('subagent', 'Worker', '/fixture', 6, 0, 'codex_vscode', 'vscode', None, None, 'subagent'),
                ('old-subagent', 'Worker', '/fixture', 5, 0, 'codex_vscode', '{"subagent":{}}', None, None, None),
            ])
        rows = self.bridge.list()
        self.assertEqual({row['id'] for row in rows}, {self.thread_id, 'orphan-ide', 'fork-ide'})
        self.assertTrue(next(row for row in rows if row['id'] == 'orphan-ide')['orphanedSideChat'])
        self.assertEqual({row['id'] for row in self.bridge.store.attached_children(self.thread_id)},
                         {'attached-app', 'attached-ide'})

    def test_independent_home_keeps_its_own_inventory_even_with_shared_ids(self):
        other = self.home / 'other-home'
        other.mkdir()
        (other / 'config.toml').write_text('model="fixture"\n')
        with closing(sqlite3.connect(other / 'state_5.sqlite')) as db, db:
            db.execute('CREATE TABLE threads(id TEXT,title TEXT,cwd TEXT,updated_at INT,archived INT,originator TEXT,source TEXT)')
            db.executemany('INSERT INTO threads VALUES(?,?,?,?,?,?,?)', [
                (self.thread_id, 'Other IDE', '/other/project', 2, 0, 'codex_vscode', 'vscode'),
                ('other-only', 'Other CLI', '/other/project', 1, 0, 'codex_cli_rs', 'cli'),
            ])
        entry = self.bridge.entries.save_local(str(other), 'Other')['entry']
        target = self.bridge.for_host(entry)
        with patch('subprocess.Popen', side_effect=AssertionError('List launched a process')), \
                patch.object(target.store, 'list', wraps=target.store.list) as other_read:
            rows = self.bridge.list()
            self.assertEqual([(row['id'], row['host'], row['cwd']) for row in rows],
                             [(self.thread_id, 'local', self.native_state['cwd'])])
            other_read.assert_not_called()
            with patch.object(self.bridge.store, 'list', side_effect=AssertionError('Read default home')):
                rows, available = self.bridge.list(entry=entry, include_sources=True)
            self.assertEqual({row['id'] for row in rows}, {self.thread_id, 'other-only'})
            self.assertEqual(available, ['cli', 'ide'])
            self.assertTrue(all(row['host'] == entry and row['cwd'] == '/other/project' for row in rows))

    def test_ssh_keeps_ide_excluded_in_host_and_aggregate_reads(self):
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute("INSERT INTO threads(id,title,cwd,updated_at,archived,originator,source) "
                       "VALUES('cli','CLI','/fixture',2,0,'codex_cli_rs','cli')")
        self.bridge.hosts.hosts = Mock(return_value={'ssh-fixture': {'alias': 'fixture'}})
        remote = self.bridge.for_host('ssh-fixture')
        # Execute the remote query against a synthetic database, without SSH.
        store = self.bridge.store
        with patch.object(remote.store, 'list', wraps=store.list), \
                patch.object(remote.store, 'available_sources', wraps=store.available_sources), \
                patch.object(remote.store, 'poll', side_effect=lambda method, args: getattr(store, method)(**args)):
            rows, available = remote.list(include_sources=True)
            self.assertEqual([row['id'] for row in rows], ['cli'])
            self.assertEqual(available, ['cli'])
            self.assertEqual(remote.list(source='ide'), [])
            rows, available = self.bridge.list(include_sources=True)
            self.assertEqual({(row['host'], row['id']) for row in rows},
                             {('local', self.thread_id), ('local', 'cli'), ('ssh-fixture', 'cli')})
            self.assertEqual(available, ['cli', 'ide'])

    def test_passive_history_preserves_identity_without_resuming(self):
        with patch('bridge.app.service.open_in_desktop') as navigation:
            self.bridge.list()
            self.bridge.view(self.thread_id, background=True)
            self.wait_for_background_read()
            view = self.bridge.view(self.thread_id, attach=False)
        self.assertFalse(view['connected'])
        self.assertEqual(view['access']['source'], 'ide')
        self.assertFalse(view['access']['canSend'])
        self.assertIn(self.private_text, str(view))
        self.assertEqual(view['provider'], self.native_state['modelProvider'])
        self.assertEqual(view['cwd'], self.native_state['cwd'])
        self.cli.attach.assert_not_called()
        self.cli.call.assert_not_called()
        navigation.assert_not_called()

    def test_absent_editor_requires_explicit_continue_with_original_identity(self):
        with self.assertRaisesRegex(IPCError, '主动继续'):
            self.bridge._target(self.thread_id)
        self.cli.attach.assert_not_called()
        self.bridge.reconnect_session(self.thread_id)
        session = self.bridge.session(self.thread_id, attach=False)
        self.assertTrue(session.connected)
        self.assertEqual(session.origin, 'ide')
        self.assertEqual(session.state['cwd'], self.native_state['cwd'])
        self.assertEqual(session.state['modelProvider'], self.native_state['modelProvider'])
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)

    def test_existing_editor_owner_requires_original_identity_and_receives_sends(self):
        from tests.test_cli_bridge_integration import CliBridgeTests
        self.cli.available.return_value = False
        with patch('bridge.app.service.open_in_desktop') as navigation:
            for mismatch in ({'cwd': '/other/project'}, {'modelProvider': 'other'}, {'hostId': 'other'}, {}):
                with self.subTest(mismatch=mismatch):
                    self.bridge.live.pop(self.thread_id, None)
                    CliBridgeTests.desktop_owner(self, {**self.native_state, **mismatch})
                    session = self.bridge.session(self.thread_id)
                    self.bridge.ipc.owner.assert_called_once_with(self.thread_id, host='local')
                    self.bridge.ipc.follow.assert_called_once_with(self.thread_id, 'desktop-owner', host='local')
                    self.assertEqual(session.connected, not mismatch)
                    self.assertEqual(session.origin, 'ide')
                    self.assertEqual(session.access['canSend'], not mismatch)
            self.assertEqual(session.owner, 'desktop-owner')
            self.assertEqual(session.runtime, 'desktop')
            self.assertEqual(session.state['cwd'], self.native_state['cwd'])
            self.assertEqual(session.state['modelProvider'], self.native_state['modelProvider'])
            self.bridge.ipc.request = Mock(return_value={'result': {'ok': True}})
            self.bridge.send(self.thread_id, 'fixture message', str(uuid.uuid4()))
            self.assertEqual(self.bridge.ipc.request.call_args.args[0], 'thread-follower-start-turn')
            self.assertEqual(self.bridge.ipc.request.call_args.kwargs['target'], 'desktop-owner')
            self.assertEqual(self.bridge.ipc.request.call_args.kwargs['host'], 'local')
        self.cli.attach.assert_not_called()
        self.cli.call.assert_not_called()
        navigation.assert_not_called()

    def test_unconfirmed_editor_owner_never_starts_another_writer(self):
        self.bridge.ipc.connect = Mock()
        for error in ('桌面读取超时；尚未取得实时会话状态。', '现有 Codex App 连接与此会话不匹配，未连接'):
            with self.subTest(error=error):
                self.bridge.live.clear()
                self.bridge.ipc.owner.side_effect = IPCError(error)
                with self.assertRaises(IPCError):
                    self.bridge.reconnect_session(self.thread_id)
                self.cli.attach.assert_not_called()


class IdeProfileTests(CliBridgeFixture):
    def setUp(self):
        super().setUp()
        with closing(sqlite3.connect(self.home/'state_5.sqlite')) as db, db:
            db.execute("UPDATE threads SET source='vscode', originator='codex_vscode'")
        self.bridge.host = 'codex:'+'1'*32
        self.bridge.entry_label = 'IDE fixture'
        absent = IPCError('desktop absent'); absent.__cause__ = FileNotFoundError()
        self.bridge.ipc.connect.side_effect = absent

    def test_ide_source_does_not_appear_as_desktop_or_cli(self):
        self.assertEqual([r['id'] for r in self.bridge.store.list(source='ide')], [self.thread_id])
        self.assertEqual(self.bridge.store.list(source='desktop'), [])
        self.assertEqual(self.bridge.store.list(source='cli'), [])
        self.assertEqual(self.bridge.store.get(self.thread_id)['origin'], 'ide')
        self.assertEqual(self.bridge.store.available_sources(), ['ide'])
        rows = self.bridge.list(local_only=True)
        self.assertEqual(rows[0]['host'], self.bridge.host)
        self.assertEqual(rows[0]['hostLabel'], 'IDE fixture')
        self.cli.attach.assert_not_called()

    def test_explicit_headless_attach_keeps_identity_and_local_filesystem(self):
        session = self.bridge.activate(self.thread_id)
        self.assertTrue(session.connected)
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)
        self.assertEqual(session.origin, 'ide')
        self.assertEqual(session.runtime, 'cli')
        self.assertEqual(self.bridge.native_host, 'local')
        project = self.bridge.register_project(str(self.home), host=self.bridge.host, kind='cli')
        self.assertEqual(project['host'], self.bridge.host)
        self.assertEqual(self.bridge.project_host(project['key']), self.bridge.host)
        self.assertEqual(self.bridge.project_environments()[0]['host'], self.bridge.host)

    def test_owner_timeout_never_starts_a_competing_writer(self):
        self.bridge.ipc.connect = Mock()
        self.bridge.ipc.owner.side_effect = IPCError('桌面读取超时；尚未取得实时会话状态。')
        with self.assertRaisesRegex(IPCError, 'VS Code 原会话未响应'):
            self.bridge.activate(self.thread_id)
        self.cli.attach.assert_not_called()

    def test_mutation_after_editor_closes_requires_explicit_headless_resume(self):
        with self.assertRaisesRegex(IPCError, '主动继续'):
            self.bridge._target(self.thread_id)
        self.cli.attach.assert_not_called()
        self.assertTrue(self.bridge.activate(self.thread_id).connected)
        self.cli.attach.assert_called_once_with(self.thread_id, explicit=True)

    def test_windows_ide_snapshot_follows_same_directory_and_reuses_verified_cwd(self):
        from unittest.mock import patch
        session = self.bridge.session(self.thread_id, attach=False)
        session.saved_cwd = r'C:\Work\Project'
        session.owner = 'editor'
        session.desktop_owner_probe = 'found'
        state = {**self.native_state, 'cwd': r'\\?\C:\Work\Project'}
        def emit(change, version=11):
            self.bridge._event({'method': 'thread-stream-state-changed', 'sourceClientId': 'editor',
                                'version': version, 'params': {'hostId': 'local',
                                'conversationId': self.thread_id, 'change': change}})
        with patch('sys.platform', 'win32'), patch('os.path.samefile', return_value=True) as same:
            emit({'type': 'snapshot', 'conversationState': state, 'revision': 1})
            self.assertTrue(session.connected)
            self.assertTrue(session.access['canEditMessages'])
            self.assertTrue(session.access['canUseSideChat'])
            self.assertTrue(session.access['canConfigure'])
            self.assertEqual(session.runtime, 'desktop')
            emit({'type': 'patches', 'baseRevision': 1, 'revision': 2, 'patches': []})
            same.assert_called_once_with(session.saved_cwd, state['cwd'])
            emit({'type': 'snapshot', 'conversationState': state, 'revision': 3}, version=7)
        self.assertFalse(session.connected)
        self.assertIn('不兼容', session.error)
        self.assertFalse(session.access['canEditMessages'])
        self.cli.attach.assert_not_called()

    def test_unrelated_legacy_stream_does_not_disconnect_current_editor(self):
        session = self.bridge.session(self.thread_id, attach=False)
        self.bridge.ipc.follow = Mock()
        session.connected, session.runtime, session.owner = True, 'desktop', 'editor'
        session.desktop_owner_probe = 'found'
        self.bridge._event({'method': 'thread-stream-state-changed', 'version': 7,
                            'sourceClientId': 'other-editor', 'params': {
                                'hostId': 'local', 'conversationId': self.thread_id, 'change': {}}})
        self.assertTrue(session.connected)

    def test_other_owner_failures_cannot_bypass_identity_checks(self):
        self.bridge.ipc.connect = Mock()
        self.bridge.ipc.owner.side_effect = IPCError('现有 Codex App 连接与此会话不匹配，未连接')
        with self.assertRaises(IPCError): self.bridge.activate(self.thread_id)
        self.cli.attach.assert_not_called()

    def test_shared_windows_pipe_refuses_a_cloned_uuid(self):
        default_store = Mock(); default_store.home = self.home
        registry = Entries(self.home/'registry', self.home)
        self.addCleanup(registry.close)
        with self.assertRaisesRegex(IPCError, '同 ID'):
            registry.check_ipc_scope(self.bridge.host, self.thread_id, default_store)
        default_store.get.side_effect = KeyError(self.thread_id)
        registry.check_ipc_scope(self.bridge.host, self.thread_id, default_store)

    def test_native_owner_release_blocks_late_snapshots_and_stale_generations(self):
        session = self.bridge.session(self.thread_id, attach=False)
        session.origin='ide'; session.connected=True; session.owner='ide-owner'; session.runtime='desktop'
        session.state=self.native_state.copy(); session.state['hostId']='local'
        self.bridge.ipc.client_id='current'; self.bridge.ipc.follow=Mock(); self.cli.cli_connections.return_value=[]
        row=self.bridge.cli_connections()[0]
        self.bridge.ipc.client_id='replacement'
        with self.assertRaisesRegex(ValueError, '变化'): row['_release'](False)
        self.bridge.ipc.follow.assert_not_called()
        self.bridge.ipc.client_id='current'
        result=row['_release'](False)
        self.assertEqual(result['status'], 'detached')
        self.assertTrue(self.bridge.released_cli.contains(self.thread_id))
        self.assertFalse(session.connected)
        self.bridge._attach(session)
        self.cli.attach.assert_not_called()
        self.bridge.ipc.follow.assert_called_once_with(self.thread_id,'ide-owner',False,host='local')


class ProfileCloseTests(ProfileRegistryTests):
    def test_disabling_drains_accepted_writes_and_blocks_stale_targets(self):
        import threading
        from unittest.mock import patch
        target=Bridge(self.other,self.root/'gateway',host=local_entry_id(self.other),codex_bin=self.root/'absent')
        self.addCleanup(target.close)
        entered,finish,closed=threading.Event(),threading.Event(),threading.Event()
        target.store.get=Mock(return_value={'cwd':str(self.other)})
        def write(*args):
            entered.set();self.assertTrue(finish.wait(3));return {'ok':True}
        with patch('bridge.app.service.workspace_operation',side_effect=write) as native:
            worker=threading.Thread(target=lambda:target.workspace(str(uuid.uuid4()),'upload',{}))
            stopper=threading.Thread(target=lambda:(target.close(),closed.set()))
            worker.start();self.assertTrue(entered.wait(2));stopper.start()
            self.assertFalse(closed.wait(.05));finish.set();worker.join(3);stopper.join(3)
            self.assertTrue(closed.is_set())
            with self.assertRaisesRegex(ValueError,'已关闭'):
                target.workspace(str(uuid.uuid4()),'upload',{})
            with self.assertRaisesRegex(ValueError,'已关闭'):
                target.register_project(str(self.other),host=target.host,kind='cli')
            self.assertEqual(native.call_count,1)

class IndependentAccountsTests(ProfileRegistryTests):
    def setUp(self):
        super().setUp()
        self.bridge = Bridge(self.default, self.root/'accounts-gateway', codex_bin=self.root/'absent')
        self.addCleanup(self.bridge.close)
        self.entry = self.bridge.entries.save_local(str(self.other), '独立 Codex')['entry']
        self.target = self.bridge.account_target(self.entry)

    def test_accounts_are_private_to_the_selected_instance(self):
        payload = {'action':'addApi','name':'A','baseUrl':'https://fixture.invalid/v1',
                   'apiKey':'synthetic-key','model':'test-model'}
        result = self.bridge.accounts_control({**payload, 'entry':self.entry})
        identifier = result['accounts'][0]['id']
        self.assertEqual(result['scope'], 'profile')
        self.assertEqual(result['codexHome'], str(self.other))
        self.assertEqual(self.bridge.accounts.public()['accounts'], [])
        self.assertNotEqual(self.target.accounts.root, self.bridge.accounts.root)
        self.assertEqual(self.target.accounts.monitor.notification_data_dir,self.bridge.data_dir)
        self.assertNotEqual(self.target.accounts.monitor.ledger_path,self.bridge.accounts.monitor.ledger_path)
        with self.assertRaises(ValueError):
            self.bridge.accounts_control({'action':'rename','id':identifier,'name':'Cross-scope'})
        self.assertFalse((self.default/'auth.json').exists())
        self.assertIsNone(result['desktopExecutable'])
        with self.assertRaisesRegex(ValueError, '默认 Codex Desktop'):
            self.bridge.accounts_control({'entry':self.entry,'action':'scanDesktop'})

    def test_disabled_and_unknown_accounts_never_fall_back_to_default(self):
        self.bridge.entries.configure(self.entry, False)
        self.assertEqual(self.bridge.accounts_control({'entry':self.entry})['scope'],'profile')
        with self.assertRaises(KeyError): self.bridge.for_host(self.entry)
        with self.assertRaises(KeyError): self.bridge.account_target('codex:'+'f'*32)
        with self.assertRaises(ValueError): self.bridge.account_target('not-a-client')

    def test_profile_switch_keeps_default_desktop_out_of_runtime_discovery(self):
        from unittest.mock import patch
        from bridge.features.accounts.cli_sync import LocalCliSync
        with patch('bridge.features.accounts.cli_sync.DesktopApp') as desktop:
            self.assertEqual(LocalCliSync(self.target)._codex_runtime(), (False, None))
            desktop.assert_not_called()

    def test_active_native_turn_blocks_profile_switch_before_signalling(self):
        from unittest.mock import patch
        from bridge.features.accounts.cli_sync import LocalCliSync
        self.target.cli_sessions.daemon_status=Mock(return_value='running')
        self.target.cli_sessions.client=Mock()
        self.target.cli_sessions.client.connect.return_value={'codexHome':str(self.other)}
        self.target.cli_sessions.client.loaded_threads.return_value=['busy']
        self.target.cli_sessions.client.request.return_value={'thread':{'id':'busy','status':{'type':'active'}}}
        with patch('bridge.clients.codex.cli_session._managed_cli',return_value=self.root/'codex'), \
             patch('bridge.platforms.codex_daemon_env.read_daemon_environment',return_value=({},('generation',))), \
             patch('bridge.platforms.codex_daemon_env.stop_daemon_gracefully') as stop:
            with self.assertRaisesRegex(ValueError,'正在运行'):
                LocalCliSync(self.target).prepare_profile_switch()
            stop.assert_not_called()

    def test_switch_waiting_for_account_read_does_not_hold_status_lock(self):
        import threading
        from unittest.mock import patch
        manager=self.target.accounts
        manager.add_api({'name':'Lock fixture','baseUrl':'https://fixture.invalid/v1','apiKey':'synthetic','model':'test'})
        row=manager.index['accounts'][-1]
        manager.info.current=Mock(return_value={'id':None})
        errors=[];started=threading.Event();attempted=threading.Event()
        lock=threading.Lock()
        class AccountLock:
            def __enter__(inner):
                attempted.set();lock.acquire()
            def __exit__(inner,*args): lock.release()
        self.target.account.lock=AccountLock()
        def change():
            started.set()
            try:
                manager.switch({'id':row['id'],'requestId':str(uuid.uuid4()),'confirmed':True,'tasksConfirmed':True})
            except Exception as error: errors.append(error)
        with patch('bridge.features.accounts.cli_sync.LocalCliSync') as sync:
            sync.return_value.preflight.return_value={'desktopRunning':False}
            sync.return_value.apply.return_value={'mutated':False}
            with lock:
                worker=threading.Thread(target=change);worker.start();self.assertTrue(started.wait(1));self.assertTrue(attempted.wait(1))
                acquired=manager.lock.acquire(timeout=.2)
                if acquired: manager.lock.release()
            worker.join(2)
        self.assertFalse(worker.is_alive());self.assertEqual(errors,[])
        self.assertTrue(acquired,'status reader must be able to finish while switch waits for account I/O')
