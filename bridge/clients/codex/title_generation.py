"""Generate a bounded model title on an already verified native CLI connection.

Temporary threads stay private to this module. The caller binds RPC and name
application to the original connection and owns enrollment and durable receipts.
"""
import json
import queue
import threading
import time
from dataclasses import dataclass

from bridge.clients.codex.app_server import AppServerError, AppServerUnknownResult
from bridge.clients.codex.ipc import IPCError

TITLE_MODEL = 'gpt-6-luna'


# These are the isolation options used by the native 0.164 TUI structured request.
_DISABLED_OPTIONS = (
    'features.apps', 'features.code_mode', 'features.code_mode_only',
    'features.context_management', 'features.current_time_reminder',
    'features.deferred_executor', 'features.enable_fanout', 'features.goals',
    'features.hooks', 'features.image_generation', 'features.memories',
    'features.multi_agent', 'features.multi_agent_v2', 'features.plugins',
    'features.request_permissions_tool', 'features.shell_snapshot', 'features.shell_tool',
    'features.standalone_web_search', 'features.token_budget', 'features.tool_suggest',
    'features.unified_exec', 'features.view_image', 'cloud.skills.enabled',
    'skills.include_instructions', 'tools.experimental_request_user_input.enabled',
    'tools.update_plan.enabled',
)
_TITLE_PROMPT_PREFIX = (
    'Generate a concise, single-line task title of at most 36 characters and under five words where possible. '
    'Start with an imperative verb. Capitalize only the first word unless the user\'s language, proper nouns, '
    'acronyms, or code terms require otherwise. Preserve ticket references exactly. Write in the user\'s language. '
    'Do not use quotes, markdown, or trailing punctuation. Do not answer the request.\n\nUser prompt:\n'
)


def _title_prompt(text):
    if not isinstance(text, str) or not text.strip():
        raise TitleCancelled('首条消息没有可用于命名的文本')
    remaining = 960 - len(_TITLE_PROMPT_PREFIX.encode('utf-8'))
    bounded = text.strip().encode('utf-8')[:remaining].decode('utf-8', errors='ignore')
    return _TITLE_PROMPT_PREFIX + bounded


def _unique_fields(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('Duplicate title field')
        value[key] = item
    return value


def _isolated_config(effective, permission_profile):
    if not isinstance(effective, dict):
        raise TitleError('无法核验命名线程的有效配置')
    servers = effective.get('mcp_servers', {})
    if not isinstance(servers, dict) or any(not isinstance(name, str) or not name for name in servers):
        raise TitleError('无法核验命名线程的 MCP 配置')
    config = {option: False for option in _DISABLED_OPTIONS}
    config.update(web_search='disabled', mcp_servers={name: {'enabled': False} for name in servers})
    if not permission_profile or permission_profile.startswith(':'):
        config['default_permissions'] = ':read-only'
    return config


class TitleError(RuntimeError):
    def __init__(self, message, *, unknown=False):
        super().__init__(message)
        self.unknown = unknown


class TitleCancelled(TitleError):
    cancelled = True


@dataclass(frozen=True)
class TitleContext:
    thread_id: str
    cwd: str
    provider: str
    connection_key: object
    request: object
    respond: object
    apply: object
    permission_profile: str | None = None
    matches_cwd: object = None


class _PrivateReceiver:
    def __init__(self):
        self.messages = queue.Queue(maxsize=64)
        self.disconnected = threading.Event()
        self.error = None
        self.turn_id = None
        self.requests = set()
        self.replied = set()


class TitleGenerator:
    def __init__(self):
        self.lock = threading.RLock()
        self.receivers = {}

    def consume(self, message, connection_key):
        if not isinstance(message, dict) or not isinstance(message.get('params'), dict):
            return False
        params = message['params']
        if not isinstance(params.get('threadId'), str):
            return False
        with self.lock:
            receiver = self.receivers.get((connection_key, params.get('threadId')))
            if receiver is None:
                return False
            method = message.get('method')
            if 'id' in message:
                # Only the request identity is needed to reject interaction; never retain tool inputs.
                request_id = message['id']
                if (not isinstance(request_id, (int, str)) or isinstance(request_id, bool) or
                        len(receiver.requests) >= 64 and request_id not in receiver.requests):
                    receiver.error = TitleError('无法核验临时命名交互请求')
                    return True
                receiver.requests.add(request_id)
                private = {'id': message['id'], 'method': method, 'params': {'threadId': params['threadId']}}
            elif method in ('turn/started', 'turn/completed'):
                turn = params.get('turn') if isinstance(params.get('turn'), dict) else {}
                if isinstance(turn.get('id'), str) and turn['id']:
                    receiver.turn_id = receiver.turn_id or turn['id']
                private = {'method': method, 'params': {'turn': {
                    'id': turn.get('id'), 'status': turn.get('status')}}}
            elif method in ('item/started', 'item/completed'):
                item = params.get('item') if isinstance(params.get('item'), dict) else {}
                text = item.get('text') if item.get('type') == 'agentMessage' else None
                if isinstance(text, str):
                    try:
                        oversized = len(text) > 8192 or len(text.encode('utf-8')) > 8192
                    except UnicodeError:
                        receiver.error = TitleError('模型标题结果不是有效 Unicode')
                        return True
                    if oversized:
                        receiver.error = TitleError('模型标题结果超过允许大小')
                        return True
                private = {'method': method, 'params': {'turnId': params.get('turnId'), 'item': {
                    'id': item.get('id'), 'type': item.get('type'), 'text': text, 'phase': item.get('phase')}}}
            elif method in ('error', 'thread/closed', 'thread/status/changed'):
                private = {'method': method, 'params': {'willRetry': params.get('willRetry'),
                           'status': params.get('status')}}
            else:
                return True
            try:
                receiver.messages.put_nowait(private)
            except queue.Full:
                receiver.error = TitleError('临时命名事件超过允许数量')
        return True

    def disconnect(self, connection_key=None):
        with self.lock:
            for (key, _), receiver in self.receivers.items():
                if connection_key is None or key == connection_key:
                    receiver.disconnected.set()

    def run(self, context, text, *, cancelled, progress, timeout=30):
        prompt = _title_prompt(text)
        deadline = time.monotonic() + timeout
        temporary_id = None
        turn_id, ended = None, False
        receiver = _PrivateReceiver()

        def reject(request_id):
            with self.lock:
                if request_id in receiver.replied:
                    return
                receiver.replied.add(request_id)
            context.respond(request_id, error={
                'code': -32000, 'message': 'Temporary title requests cannot use tools or request interaction'})

        def notify(stage, **fields):
            if (cancelled.is_set() or receiver.disconnected.is_set() or
                    progress(stage, **fields) is False or cancelled.is_set() or receiver.disconnected.is_set()):
                raise TitleCancelled('自动命名已取消')

        def matching_cwd(value):
            return (context.matches_cwd(context.cwd, value) if context.matches_cwd is not None
                    else context.cwd == value)

        def request(method, params):
            if cancelled.is_set() or receiver.disconnected.is_set():
                raise TitleCancelled('自动命名已取消')
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TitleError('自动命名超时')
            try:
                result = context.request(method, params, timeout=remaining)
                if method == 'turn/start' and isinstance(result, dict):
                    returned_turn = result.get('turn')
                    if isinstance(returned_turn, dict) and isinstance(returned_turn.get('id'), str):
                        with self.lock:
                            receiver.turn_id = returned_turn['id']
                if receiver.disconnected.is_set():
                    raise TitleCancelled('原命名连接已断开')
                return result
            except (AppServerError, IPCError) as error:
                uncertain = (isinstance(error, AppServerUnknownResult) or
                             isinstance(error, IPCError) and not hasattr(error, 'rpc_error') and
                             not getattr(error, 'not_sent', False))
                raise TitleError('原生自动命名请求未完成',
                                 unknown=uncertain and method in ('thread/start', 'turn/start')) from error

        try:
            source = request('thread/read', {'threadId': context.thread_id, 'includeTurns': False})['thread']
            if (source.get('id') != context.thread_id or not matching_cwd(source.get('cwd')) or
                    source.get('modelProvider') != context.provider):
                raise TitleCancelled('原聊天身份已变化')
            if (source.get('status') or {}).get('type') not in ('idle', 'active'):
                raise TitleCancelled('原聊天已关闭')
            if isinstance(source.get('name'), str) and source['name']:
                raise TitleCancelled('原聊天已有名称')
            if source.get('name') not in (None, ''):
                raise TitleError('无法核验原聊天名称')
            effective = request('config/read', {'cwd': context.cwd, 'includeLayers': False}).get('config')
            config = _isolated_config(effective, context.permission_profile)
            model = TITLE_MODEL
            notify('starting')
            start_params = {
                'cwd': context.cwd, 'modelProvider': context.provider, 'model': model,
                'ephemeral': True, 'runtimeWorkspaceRoots': [],
                'dynamicTools': [], 'environments': [], 'selectedCapabilityRoots': [],
                'config': config, 'threadSource': 'thread_title'}
            custom_profile = context.permission_profile and not context.permission_profile.startswith(':')
            if custom_profile:
                start_params['permissions'] = context.permission_profile
            else:
                start_params['sandbox'] = 'read-only'
            result = request('thread/start', start_params)
            thread = result.get('thread') or {}
            temporary_id = thread.get('id')
            if not isinstance(temporary_id, str) or not temporary_id or temporary_id == context.thread_id:
                temporary_id = None
                raise TitleError('原生运行时没有确认临时命名线程')
            # Retain the acknowledged creation ID even when later validation rejects model execution.
            notify('starting', temporary_thread_id=temporary_id)
            if thread.get('ephemeral') is not True:
                raise TitleError('原生运行时没有确认临时命名线程')
            if (not matching_cwd(thread.get('cwd')) or thread.get('modelProvider') != context.provider or
                    result.get('model') != model or
                    result.get('modelProvider', context.provider) != context.provider):
                raise TitleError('原生临时命名线程的目录、提供商或模型不匹配')
            if custom_profile:
                if (result.get('activePermissionProfile') or {}).get('id') != context.permission_profile:
                    raise TitleError('原生临时命名线程没有保留受管权限配置')
            elif (result.get('sandbox') or {}).get('type') != 'readOnly':
                raise TitleError('原生临时命名线程没有确认只读权限')
            with self.lock:
                if (context.connection_key, temporary_id) in self.receivers:
                    temporary_id = None
                    raise TitleError('临时命名线程身份重复')
                self.receivers[(context.connection_key, temporary_id)] = receiver
            notify('generating', temporary_thread_id=temporary_id)
            turn_params = {
                'threadId': temporary_id,
                'effort': 'low',
                'input': [{'type': 'text', 'text': prompt}],
                'outputSchema': {'type': 'object', 'properties': {
                    'title': {'type': 'string', 'minLength': 1, 'maxLength': 36}},
                    'required': ['title'], 'additionalProperties': False}}
            turn = request('turn/start', turn_params)['turn']
            turn_id = turn.get('id')
            if not turn_id:
                raise TitleError('原生运行时没有确认临时命名任务')
            notify('generating', temporary_thread_id=temporary_id, turn_id=turn_id)
            response = None
            while True:
                if cancelled.is_set() or receiver.disconnected.is_set():
                    raise TitleCancelled('自动命名已取消')
                if receiver.error is not None:
                    raise receiver.error
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TitleError('自动命名超时')
                try:
                    message = receiver.messages.get(timeout=min(.1, remaining))
                except queue.Empty:
                    continue
                params = message.get('params') or {}
                if 'id' in message and message.get('method'):
                    reject(message['id'])
                    raise TitleError('临时命名任务请求了不允许的交互')
                if message.get('method') == 'thread/closed':
                    raise TitleCancelled('原生临时命名线程已关闭')
                if message.get('method') == 'thread/status/changed':
                    status = params.get('status') if isinstance(params.get('status'), dict) else {}
                    if status.get('type') not in ('active', 'idle'):
                        raise TitleCancelled('原生临时命名线程不可用')
                if message.get('method') == 'error' and params.get('willRetry') is not True:
                    raise TitleError('原生临时命名任务失败')
                if message.get('method') in ('item/started', 'item/completed') and params.get('turnId') == turn_id:
                    if (params.get('item') or {}).get('type') not in ('userMessage', 'reasoning', 'agentMessage'):
                        raise TitleError('临时命名任务使用了不允许的工具')
                if message.get('method') == 'item/completed' and params.get('turnId') == turn_id:
                    item = params.get('item') or {}
                    if item.get('type') == 'agentMessage' and item.get('phase') in (None, 'final_answer'):
                        response = item.get('text')
                        if (not isinstance(response, str) or len(response) > 8192 or
                                len(response.encode('utf-8')) > 8192):
                            raise TitleError('模型标题结果超过允许大小')
                elif message.get('method') == 'turn/completed' and (params.get('turn') or {}).get('id') == turn_id:
                    ended = True
                    if params['turn'].get('status') != 'completed':
                        raise TitleError('临时命名任务未完成')
                    break
            try:
                value = json.loads(response, object_pairs_hook=_unique_fields)
            except (TypeError, ValueError) as error:
                raise TitleError('模型没有返回有效标题') from error
            if not isinstance(value, dict) or set(value) != {'title'} or not isinstance(value['title'], str):
                raise TitleError('模型没有返回有效标题')
            title = value['title'].strip()
            if (not title or len(title) > 36 or any(
                    ord(character) < 32 or 127 <= ord(character) < 160 or
                    0xD800 <= ord(character) <= 0xDFFF or character in '\u2028\u2029' for character in title)):
                raise TitleError('模型没有返回有效标题')
            notify('generated', title=title)
            notify('applying')
            if cancelled.is_set() or not context.apply(title, cancelled):
                raise TitleCancelled('原聊天已变化，自动名称未写入')
            return title
        except TitleError:
            raise
        except (AppServerError, IPCError) as error:
            uncertain = (isinstance(error, AppServerUnknownResult) or
                         isinstance(error, IPCError) and not hasattr(error, 'rpc_error') and
                         not getattr(error, 'not_sent', False))
            raise TitleError('自动名称写入未确认', unknown=uncertain) from error
        finally:
            if temporary_id:
                with self.lock:
                    pending_requests = receiver.requests - receiver.replied
                for request_id in pending_requests:
                    try:
                        reject(request_id)
                    except Exception:
                        pass
                turn_id = turn_id or receiver.turn_id
                if turn_id and not ended:
                    try:
                        context.request('turn/interrupt', {'threadId': temporary_id, 'turnId': turn_id}, timeout=2)
                    except Exception:
                        pass
                try:
                    context.request('thread/unsubscribe', {'threadId': temporary_id}, timeout=2)
                except Exception:
                    pass
                with self.lock:
                    if self.receivers.get((context.connection_key, temporary_id)) is receiver:
                        self.receivers.pop((context.connection_key, temporary_id), None)
