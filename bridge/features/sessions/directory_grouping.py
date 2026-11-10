"""Pure directory display metadata; never changes native project or cwd identity."""
import json
import posixpath
import re


def directory_group(cwd, *, scope, target_path_style=None):
    """Scope is (client family, complete routing host), not the native host ID."""
    recorded = cwd if isinstance(cwd, str) and cwd else None
    if recorded is None:
        style = 'missing'
    elif re.match(r'^[A-Za-z]:[\\/]', recorded) or recorded.startswith('\\\\') or re.match(r'^//[?.]/', recorded):
        style = 'windows'
    elif recorded.startswith('//'):
        style = {'nt': 'windows', 'windows': 'windows', 'posix': 'posix'}.get(target_path_style, 'opaque')
    else:
        style = 'posix' if recorded.startswith('/') else 'opaque'
    path = recorded
    if style == 'windows':
        path = path.replace('\\', '/')
        if path[:8].lower() in ('//?/unc/', '//./unc/'):
            path = '//' + path[8:]
        elif re.match(r'^//[?.]/[A-Za-z]:/', path):
            path = path[4:]
        if not re.fullmatch(r'[A-Za-z]:/+', path):
            path = path.rstrip('/')
        else:
            path = path[:2] + '/'
    elif style == 'posix':
        path = path.rstrip('/') or ('//' if path == '//' else '/')
    key = json.dumps(['directory-v1', *scope, style, path.lower() if style == 'windows' else path],
                     ensure_ascii=False, separators=(',', ':'))
    name = (posixpath.basename(path.rstrip('/') if style == 'opaque' else path) or path) if path else None
    return {'key': key, 'name': name, 'path': recorded, 'missing': recorded is None}
