"""Directory display identity is independent of native project membership."""
import unittest

from bridge.features.sessions.directory_grouping import directory_group


class DirectoryGroupingTests(unittest.TestCase):
    def group(self, cwd, host='local', style='posix'):
        return directory_group(cwd, scope=('codex', host), target_path_style=style)

    def test_full_recorded_directory_groups_without_a_native_project(self):
        group = self.group('/work/shop')
        self.assertEqual(group['name'], 'shop')
        self.assertEqual(group['path'], '/work/shop')
        self.assertFalse(group['missing'])
        self.assertEqual(group['key'], self.group('/work/shop/')['key'])
        for other in ('/backup/shop', '/work/shop/api', '/worktrees/shop-feature'):
            self.assertNotEqual(group['key'], self.group(other)['key'])

    def test_windows_spellings_share_a_directory_on_any_gateway(self):
        for paths, name in [
            (['C:/Work/Shop', 'c:\\work\\shop\\', '\\\\?\\C:\\Work\\Shop'], 'Shop'),
            (['\\\\server\\share\\Shop', '//server/share/shop/', '\\\\?\\UNC\\server\\share\\SHOP'], 'Shop'),
            (['C:/', 'c:\\', '\\\\?\\C:\\'], 'C:/'),
        ]:
            with self.subTest(paths=paths):
                values = [self.group(path, style='windows') for path in paths]
                self.assertEqual(len({value['key'] for value in values}), 1)
                self.assertEqual(values[0]['name'], name)
                self.assertEqual([value['path'] for value in values], paths)
        self.assertEqual(self.group('C:/Work/Shop', style='posix')['key'],
                         self.group('c:\\work\\shop', style='windows')['key'])

    def test_missing_directory_has_its_own_scoped_identity(self):
        for value in (None, '', 4):
            group = self.group(value)
            self.assertEqual({k: group[k] for k in ('name', 'path', 'missing')},
                             {'name': None, 'path': None, 'missing': True})
            self.assertEqual(group['key'], self.group(None)['key'])
        for value in (None, '/work/shop'):
            self.assertNotEqual(self.group(value)['key'], self.group(value, host='ssh-a')['key'])
            self.assertNotEqual(self.group(value, host='codex:one')['key'],
                                self.group(value, host='codex:two')['key'])
        self.assertNotEqual(self.group(None)['key'], self.group('/无工作目录')['key'])

    def test_posix_keeps_case_backslashes_spaces_and_dotdot_on_windows_gateway(self):
        paths = ['/work/Shop', '/work/shop', '/work/shop ', '/work/a\\b', '/work/a/b',
                 '/work/link/../shop', '/work/中文', '/']
        groups = [self.group(path, style='windows') for path in paths]
        self.assertEqual(len({group['key'] for group in groups}), len(paths))
        self.assertEqual([group['name'] for group in groups],
                         ['Shop', 'shop', 'shop ', 'a\\b', 'b', 'shop', '中文', '/'])
        self.assertEqual([group['path'] for group in groups], paths)
        self.assertEqual(groups[1]['key'], self.group('/work/shop', style='posix')['key'])

    def test_ambiguous_double_slash_uses_target_or_keeps_unknown_spelling(self):
        upper, lower = '//server/share/Shop', '//server/share/shop/'
        self.assertEqual(self.group(upper, style='windows')['key'], self.group(lower, style='nt')['key'])
        self.assertNotEqual(self.group(upper, style='posix')['key'], self.group(lower, style='posix')['key'])
        self.assertNotEqual(self.group(lower, style=None)['key'], self.group(lower[:-1], style=None)['key'])
        self.assertEqual(self.group('//', style='posix')['name'], '//')
        self.assertNotEqual(self.group('//', style='posix')['key'], self.group('/')['key'])


if __name__ == '__main__':
    unittest.main()
