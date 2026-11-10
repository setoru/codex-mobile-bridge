"""List contracts for directory display, using synthetic histories only."""
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from unittest.mock import Mock, patch

from bridge.app.service import Bridge
from bridge.clients.codex.remote import RemoteStore
from tests.test_cli_bridge_integration import CliBridgeFixture


class DirectoryListTests(CliBridgeFixture):
    def test_sources_share_directory_without_overwriting_native_membership(self):
        with closing(sqlite3.connect(self.home / 'state_5.sqlite')) as db, db:
            db.execute('ALTER TABLE threads ADD COLUMN project_id TEXT')
            db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT)')
            db.execute("INSERT INTO projects VALUES ('native', 'Native project')")
            db.execute("UPDATE threads SET project_id='native'")
            for identifier, originator, source in [('app', 'Codex Desktop', 'vscode'),
                                                    ('ide', 'codex_vscode', 'vscode')]:
                db.execute('INSERT INTO threads(id,title,cwd,updated_at,archived,originator,source) VALUES(?,?,?,?,?,?,?)',
                           (identifier, identifier, self.native_state['cwd'], 2, 0, originator, source))
        rows = self.bridge.list()
        self.assertEqual({row['origin'] for row in rows}, {'cli', 'desktop', 'ide'})
        self.assertEqual(len({row['directoryGroup']['key'] for row in rows}), 1)
        self.assertEqual({row['directoryGroup']['name'] for row in rows}, {'project'})
        self.assertEqual({row['cwd'] for row in rows}, {self.native_state['cwd']})
        self.assertEqual({row['projectKey'] for row in rows}, {'local|native', 'local|unassigned'})
        self.assertEqual(next(row['projectName'] for row in rows if row['id'] == self.thread_id), 'Native project')
        self.cli.attach.assert_not_called()
        self.cli.call.assert_not_called()
        self.bridge.ipc.connect.assert_not_called()

    def test_independent_entry_directory_uses_routing_identity(self):
        host = 'codex:' + 'f' * 32
        other = Bridge(self.home, self.home / 'other-bridge', host=host, codex_bin=self.home / 'no-codex',
                       cli_sessions=Mock())
        self.addCleanup(other.close)
        local, independent = self.bridge.list()[0], other.list()[0]
        self.assertEqual(local['cwd'], independent['cwd'])
        self.assertEqual(independent['host'], host)
        self.assertNotEqual(local['directoryGroup']['key'], independent['directoryGroup']['key'])

    def test_remote_payload_and_cached_list_preserve_target_path_style(self):
        store = RemoteStore('fixture')
        def run_payload(alias, source, timeout):
            self.assertEqual(alias, 'fixture')
            guard = "import importlib.util\nassert importlib.util.find_spec('bridge') is None\n"
            result = subprocess.run([sys.executable, '-I', '-B', '-c', guard + source],
                                    cwd=self.home, env={**os.environ, 'CODEX_HOME': str(self.home)},
                                    capture_output=True, text=True, check=True, timeout=timeout)
            return json.loads(result.stdout)
        with patch('bridge.clients.codex.remote.ssh_read', side_effect=run_payload) as transport:
            first = store.list()
            self.assertEqual(first[0]['_targetPathStyle'], 'windows' if os.name == 'nt' else 'posix')
            first[0].pop('_targetPathStyle')
            self.assertIn('_targetPathStyle', store.list()[0])
        self.assertEqual(transport.call_count, 1)

        remote = Bridge(self.home, self.home / 'remote-bridge', host='ssh-fixture', alias='fixture',
                        cli_sessions=Mock())
        self.addCleanup(remote.close)
        with patch('bridge.clients.codex.remote.ssh_read', side_effect=run_payload):
            rows = remote.list()
        self.assertNotIn('_targetPathStyle', rows[0])
        self.assertEqual(rows[0]['directoryGroup']['path'], self.native_state['cwd'])
        self.assertNotEqual(rows[0]['directoryGroup']['key'], self.bridge.list()[0]['directoryGroup']['key'])
