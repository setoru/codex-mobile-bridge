"""Create-and-send recovery receipts, kept in the existing creation ledger."""
import copy
import re
import uuid
from urllib.parse import quote


def recovery_settings(state):
    latest = state.get('latestThreadSettings') or {}
    settings = {'modelProvider': state.get('modelProvider'),
                'model': latest.get('model') or state.get('latestModel')}
    if not all(isinstance(value, str) and value for value in settings.values()):
        raise ValueError('尚未取得源会话的模型配置，请先核对原会话')
    effort = latest.get('effort', state.get('latestReasoningEffort'))
    if effort is not None:
        settings['config'] = {'model_reasoning_effort': effort}
    if 'serviceTier' in latest:
        settings['serviceTier'] = latest['serviceTier']
    permissions = {**(state.get('currentPermissions') or {}), **latest}
    profile = permissions.get('activePermissionProfile') or {}
    permission = profile.get('id')
    if not permission:
        policy = permissions.get('sandboxPolicy') or {}
        # Only exact built-in profiles can be inferred from a saved policy.
        permission = {'dangerFullAccess': ':danger-full-access',
                      'danger-full-access': ':danger-full-access'}.get(policy.get('type'))
        if policy.get('type') in ('workspaceWrite', 'workspace-write'):
            raise ValueError('源会话未提供可核验的工作区权限配置，请先核对原会话')
    if not permission or 'approvalPolicy' not in permissions or 'approvalsReviewer' not in permissions:
        raise ValueError('尚未取得源会话的完整权限配置，请先核对原会话')
    settings.update(permissions=permission, approvalPolicy=permissions['approvalPolicy'],
                    approvalsReviewer=permissions['approvalsReviewer'])
    if 'runtimeWorkspaceRoots' in permissions:
        settings['runtimeWorkspaceRoots'] = copy.deepcopy(permissions['runtimeWorkspaceRoots'])
    return settings


def recovery_message(title, source_id, host):
    uuid.UUID(source_id)
    label = ' '.join(str(title or '未命名聊天').split())
    label = re.sub(r'([\\`*_{}\[\]()<>!|])', r'\\\1', label)
    link = 'codex://threads/' + source_id
    if host != 'local':
        link += '?hostId=' + quote(host, safe='')
    return '读取会话[' + label + '](' + link + ')，继续完成'


def verify_recovery_permissions(state, source):
    expected = source.get('sandboxPolicy')
    if expected is not None:
        permissions = {**(state.get('currentPermissions') or {}), **(state.get('latestThreadSettings') or {})}
        if permissions.get('sandboxPolicy') != expected:
            raise ValueError('新会话的实际工作区或网络权限与源会话不一致，尚未发送，请先核对')


class RecoveryCommands:
    def __init__(self, entries, lock, save, host):
        self.entries, self.lock, self.save, self.host = entries, lock, save, host
        self.active = set()
        for entry in entries.values():
            if entry.get('recoverySource') and entry.get('status') == 'running':
                entry.update(status='failed' if entry['phase'] == 'validating' else 'unknown',
                             message='上次操作中断，请核对已创建会话；不会自动重复提交')

    def _public(self, entry):
        if entry is None:
            return {'operationId': None, 'status': 'idle', 'id': None, 'host': self.host}
        return {key: copy.deepcopy(entry.get(key)) for key in
                ('operationId', 'status', 'phase', 'id', 'host', 'message', 'text')}

    def _find(self, source_id, request_id):
        if request_id:
            uuid.UUID(request_id)
            entry = self.entries.get(request_id)
            if entry and entry.get('recoverySource') != source_id:
                raise ValueError('同一操作标识不能用于不同会话或操作')
            return entry
        return next((entry for entry in reversed(list(self.entries.values()))
                     if entry.get('recoverySource') == source_id and
                     entry.get('status') in ('running', 'unknown', 'not_submitted')), None)

    def read(self, source_id, request_id=None, reconcile=None):
        uuid.UUID(source_id)
        with self.lock:
            entry = self._find(source_id, request_id)
            snapshot = copy.deepcopy(entry)
        if snapshot and snapshot['status'] == 'unknown' and reconcile:
            updates = reconcile(snapshot)
            if updates:
                with self.lock:
                    if entry['status'] == 'unknown' and entry['operationId'] not in self.active:
                        entry.update(updates)
                        self.save()
        with self.lock:
            return self._public(entry)

    def perform(self, source_id, request_id, *, prepare, create, configure, send, receipt, execute):
        uuid.UUID(source_id)
        uuid.UUID(request_id)
        with self.lock:
            entry = self._find(source_id, request_id)
            if entry is None:
                entry = self._find(source_id, None)
            if entry is not None and (entry['operationId'] in self.active or
                                      entry['status'] in ('confirmed', 'unknown', 'failed')):
                return self._public(entry)
            if entry is None:
                entry = {'recoverySource': source_id, 'operationId': request_id, 'id': None,
                         'host': self.host, 'status': 'running', 'phase': 'validating',
                         'message': '正在读取源会话设置…', 'title': '', 'project': None}
                self.entries[request_id] = entry
            identifier = entry['operationId']
            self.active.add(identifier)
            self.save()

        def update(**values):
            with self.lock:
                entry.update(values)
                self.save()

        def created(sid):
            uuid.UUID(sid)
            if sid == source_id:
                raise ValueError('新会话身份与源会话相同，未继续操作')
            update(id=sid)

        def run():
            if not entry.get('sourceSnapshot'):
                snapshot = prepare()
                update(sourceSnapshot=snapshot, cwd=snapshot['cwd'], kind=snapshot['kind'],
                       text=snapshot['text'])
            if not entry.get('id'):
                update(status='running', phase='creating', message='正在创建…')
                create(entry, lambda value=True: update(createStarted=value), created)
                update(createConfirmed=True)
            update(status='running', phase='settings', message='正在确认会话设置…')
            configure(entry, lambda: update(settingsStarted=True))
            update(settingsConfirmed=True, phase='sending', message='正在发送…')
            result = send(entry)
            accepted = result.get('status') == 'accepted'
            update(status='confirmed' if accepted else 'unknown',
                   phase='complete' if accepted else 'sending',
                   message='已创建新会话并发送' if accepted else '发送结果尚未确认，请核对新会话；不会重复发送')

        try:
            execute(run)
        except Exception as error:
            phase = entry['phase']
            status = 'failed'
            if phase == 'creating':
                status = 'unknown' if entry.get('createStarted') else 'failed'
            elif phase == 'settings':
                status = 'unknown' if entry.get('settingsStarted') else 'not_submitted'
            elif phase == 'sending':
                outcome = receipt(entry)
                status = 'confirmed' if outcome == 'accepted' else 'unknown' if outcome else 'not_submitted'
            update(status=status, message=str(error), **({'phase': 'complete'} if status == 'confirmed' else {}))
        finally:
            with self.lock:
                self.active.discard(identifier)
        with self.lock:
            return self._public(entry)
