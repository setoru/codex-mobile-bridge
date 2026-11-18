"""Prepare an ordinary new chat from a source chat's directory and model."""
import copy
import re
import uuid
from urllib.parse import quote
from bridge.features.sessions.create import thread_service_tier


def recovery_settings(state, metadata=None):
    """Copy known model parameters; every other setting keeps its new-chat default."""
    metadata = metadata or {}
    latest = state.get('latestThreadSettings') or {}
    model = latest.get('model') or state.get('latestModel') or metadata.get('model')
    settings = {}
    if model:
        settings['model'] = model
        effort = latest.get('effort', state.get('latestReasoningEffort'))
        if effort is not None:
            settings['effort'] = effort
    missing = object()
    tier = thread_service_tier(state, missing)
    if tier is not missing:
        settings['serviceTier'] = tier
    return settings


def recovery_message(title, source_id, host):
    uuid.UUID(source_id)
    label = ' '.join(str(title or '未命名聊天').split())
    label = re.sub(r'([\\`*_{}\[\]()<>!|])', r'\\\1', label)
    link = 'codex://threads/' + source_id
    if host != 'local':
        link += '?hostId=' + quote(host, safe='')
    return '读取会话[' + label + '](' + link + ')，继续完成'


def recovery_source(state, metadata, source_id, host, *, kind):
    """Resolve only server-owned source information, without attaching its runtime."""
    uuid.UUID(source_id)
    cwd = (state or {}).get('cwd') or metadata.get('cwd')
    if not isinstance(cwd, str) or not cwd:
        raise ValueError('源会话未记录工作目录，无法新建聊天')
    options = recovery_settings(state or {}, metadata)
    result = {'cwd': cwd, 'kind': kind, 'modelOptions': options,
              'text': recovery_message((state or {}).get('title') or metadata.get('title'), source_id, host)}
    if 'model' not in options:
        result['warning'] = '源会话未记录模型，已使用新聊天的默认模型'
    return result


class RecoveryCommands:
    """Keep the ordinary creation ID; sending belongs to the target chat's composer."""
    def __init__(self, entries, lock, save, host):
        self.entries, self.lock, self.save, self.host = entries, lock, save, host
        for entry in entries.values():
            if entry.get('recoverySource') and entry.get('status') in ('running', 'creating'):
                entry.update(status='unknown' if entry.get('createStarted') or entry.get('id') else 'failed',
                             ready=False, message='上次创建结果尚未确认，请检查聊天列表；不会重复创建')

    def _public(self, entry):
        if entry is None:
            return {'operationId': None, 'status': 'idle', 'id': None, 'host': self.host, 'ready': False}
        result = {key: copy.deepcopy(entry.get(key)) for key in
                  ('operationId', 'status', 'id', 'host', 'text', 'ready', 'opened', 'message',
                   'warning', 'draftAccountIdentity')}
        # Older operations may already have sent. Never use a read of them to
        # schedule a new first message or rewrite their inherited settings.
        if entry.get('recoveryVersion') != 2:
            result['ready'] = False
            if result.get('id'):
                result['status'] = 'created'
            if entry.get('phase') == 'sending' or entry.get('status') == 'confirmed':
                result['text'] = ''
        return result

    def _find(self, source_id, request_id):
        if request_id is None:
            return None
        uuid.UUID(request_id)
        entry = self.entries.get(request_id)
        if entry is not None and entry.get('recoverySource') != source_id:
            raise ValueError('同一创建请求不能用于不同会话或操作')
        return entry

    def read(self, source_id, request_id=None):
        uuid.UUID(source_id)
        with self.lock:
            return self._public(self._find(source_id, request_id))

    def perform(self, source_id, request_id, *, prepare, create, execute=None):
        uuid.UUID(source_id)
        uuid.UUID(request_id)
        with self.lock:
            entry = self._find(source_id, request_id)
            if entry is not None:
                return self._public(entry)
            entry = {'recoverySource': source_id, 'recoveryVersion': 2, 'operationId': request_id,
                     'id': None, 'host': self.host, 'status': 'creating', 'phase': 'validating',
                     'ready': False, 'title': '', 'project': None}
            self.entries[request_id] = entry
            self.save()

        def update(**values):
            with self.lock:
                entry.update(values)
                self.save()

        def started(value=True):
            update(createStarted=value)

        def created(identifier):
            uuid.UUID(identifier)
            if identifier == source_id:
                raise ValueError('新会话身份与源会话相同，未继续操作')
            update(id=identifier)

        def run():
            source = prepare()
            update(sourceSnapshot=source, cwd=source['cwd'], kind=source['kind'], text=source['text'],
                   warning=source.get('warning'), phase='creating')
            result = create(entry, started, created)
            update(**{'status': 'created', 'ready': True, 'phase': 'complete', **(result or {})})

        try:
            (execute or (lambda action: action()))(run)
        except Exception as error:
            status = 'created' if entry.get('id') else 'unknown' if entry.get('createStarted') else 'failed'
            update(status=status, ready=False, message=str(error))
        with self.lock:
            return self._public(entry)
