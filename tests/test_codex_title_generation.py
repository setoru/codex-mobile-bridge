"""Model titles through the Codex protocol boundary, using a synthetic runtime."""
import copy
import threading
import unittest
from dataclasses import replace

from bridge.clients.codex.app_server import AppServerError, AppServerUnavailable, AppServerUnknownResult, CodexAppServer
from bridge.clients.codex.ipc import IPCError
from bridge.clients.codex.shared_server import SharedServer
from bridge.clients.codex.title_generation import TitleCancelled, TitleContext, TitleError, TitleGenerator
from test_codex_app_server import FixtureProcess


class TitleRuntime:
    def __init__(self, generator, *, provider='fixture-provider', model='fixture-model'):
        self.generator = generator
        self.key = object()
        self.provider, self.model = provider, model
        self.calls, self.replies, self.stages = [], [], []
        self.name = None
        self.output = '{"title":"Fix login timeout"}'
        self.account = {'type': 'chatgpt'}
        self.models = [{'model': 'gpt-5.6-luna'}]
        self.start_overrides = {}
        self.source_overrides = {}
        self.final_phase = 'final_answer'
        self.failures = {}
        self.config = {'model_provider': self.provider, 'model': self.model,
                       'mcp_servers': {'fixture': {'command': '/fixture/mcp'}}}
        self.turn_accepted = threading.Event()
        self.delay_output = False
        self.extra_events = []
        self.context = TitleContext(
            thread_id='source', cwd='/fixture/project', provider=provider,
            connection_key=self.key, request=self.request, respond=self.respond,
            apply=self.apply)

    def request(self, method, params, *, timeout):
        self.calls.append((method, copy.deepcopy(params)))
        if method in self.failures:
            raise self.failures[method]
        if method == 'thread/read':
            thread = {'id': 'source', 'cwd': '/fixture/project', 'modelProvider': self.provider,
                      'name': self.name, 'status': {'type': 'active'}}
            if self.model is not None:
                thread['model'] = self.model
            thread.update(self.source_overrides)
            return {'thread': thread}
        if method == 'config/read':
            return {'config': copy.deepcopy(self.config)}
        if method == 'account/read':
            return {'account': copy.deepcopy(self.account)}
        if method == 'model/list':
            return {'data': copy.deepcopy(self.models), 'nextCursor': None}
        if method == 'thread/start':
            result = {'thread': {'id': 'temporary', 'cwd': '/fixture/project',
                                 'modelProvider': self.provider, 'ephemeral': True},
                      'model': params['model'], 'modelProvider': self.provider,
                      'sandbox': {'type': 'readOnly'}}
            for key, value in self.start_overrides.items():
                if key == 'thread':
                    result['thread'].update(value)
                else:
                    result[key] = value
            return result
        if method == 'turn/start':
            self.turn_accepted.set()
            if self.delay_output:
                return {'turn': {'id': 'title-turn', 'status': 'inProgress'}}
            # A real daemon can deliver completion before turn/start returns.
            for message in (*self.extra_events,
                    {'method': 'item/completed', 'params': {
                        'threadId': 'temporary', 'turnId': 'title-turn',
                        'item': {'id': 'answer', 'type': 'agentMessage', 'text': self.output,
                                 'phase': self.final_phase}}},
                    {'method': 'turn/completed', 'params': {
                        'threadId': 'temporary', 'turn': {'id': 'title-turn', 'status': 'completed'}}}):
                if not self.generator.consume(message, self.key):
                    raise AssertionError('Temporary title event escaped its private receiver')
            return {'turn': {'id': 'title-turn', 'status': 'completed'}}
        if method == 'thread/name/set':
            self.name = params['name']
            return {}
        if method in ('thread/unsubscribe', 'turn/interrupt'):
            return {}
        raise AssertionError('Unexpected title operation: ' + method)

    def respond(self, request_id, *, result=None, error=None):
        self.replies.append((request_id, result, error))

    def apply(self, title, cancelled):
        if cancelled.is_set() or self.name not in (None, ''):
            return False
        self.request('thread/name/set', {'threadId': 'source', 'name': title}, timeout=1)
        return True

    def progress(self, stage, **fields):
        self.stages.append((stage, fields))


class CodexTitleGenerationTests(unittest.TestCase):
    def test_generates_a_model_title_without_adding_a_turn_to_the_source(self):
        generator = TitleGenerator()
        native = TitleRuntime(generator)
        title = generator.run(native.context, 'Fix the login timeout',
                              cancelled=threading.Event(), progress=native.progress)
        self.assertEqual(title, 'Fix login timeout')
        self.assertEqual(native.name, 'Fix login timeout')
        turns = [params for method, params in native.calls if method == 'turn/start']
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]['threadId'], 'temporary')
        self.assertEqual([params for method, params in native.calls if method == 'thread/unsubscribe'],
                         [{'threadId': 'temporary'}])
        self.assertEqual(native.stages[-2:], [('generated', {'title': title}), ('applying', {})])

    def test_title_task_cannot_use_the_projects_tools_or_mcp_servers(self):
        generator = TitleGenerator()
        native = TitleRuntime(generator)
        generator.run(native.context, 'Fix the login timeout',
                      cancelled=threading.Event(), progress=native.progress)
        params = next(params for method, params in native.calls if method == 'thread/start')
        self.assertEqual(params['config']['mcp_servers'], {'fixture': {'enabled': False}})
        for option in ('features.shell_tool', 'features.unified_exec', 'features.hooks',
                       'features.memories', 'features.plugins', 'features.multi_agent',
                       'cloud.skills.enabled', 'skills.include_instructions'):
            self.assertIs(params['config'][option], False)
        self.assertEqual(params['config']['web_search'], 'disabled')
        self.assertEqual(params['config']['default_permissions'], ':read-only')
        self.assertEqual(params['dynamicTools'], [])
        self.assertEqual(params['environments'], [])
        self.assertEqual(params['runtimeWorkspaceRoots'], [])
        self.assertEqual(params['selectedCapabilityRoots'], [])
        self.assertNotIn('approvalPolicy', params)

    def test_always_uses_gpt_6_luna_on_the_original_provider_without_account_or_catalog_reads(self):
        for provider, account, models, main_model in (
                ('openai', {'type': 'chatgpt'}, [{'model': 'gpt-5.6-luna'}], 'fixture-model'),
                ('openai', {'type': 'apiKey'}, [{'model': 'gpt-6-luna'}], 'fixture-model'),
                ('openai', {'type': 'chatgpt'}, [], 'gpt-6-astra'),
                ('custom', {'type': 'chatgpt'}, [{'model': 'gpt-5.6-luna'}], 'unrelated-model'),
                ('custom', None, [], None),
                ('openai', None, [], '')):
            with self.subTest(provider=provider, account=account, main_model=main_model):
                generator = TitleGenerator()
                native = TitleRuntime(generator, provider=provider, model=main_model)
                native.account, native.models = account, models
                if main_model is None:
                    native.config.pop('model')
                generator.run(native.context, 'Fix the login timeout',
                              cancelled=threading.Event(), progress=native.progress)
                start = next(params for method, params in native.calls if method == 'thread/start')
                self.assertEqual(start['model'], 'gpt-6-luna')
                self.assertEqual(start['modelProvider'], provider)
                turn = next(params for method, params in native.calls if method == 'turn/start')
                self.assertEqual(turn['effort'], 'low')
                self.assertNotIn('account/read', [method for method, _ in native.calls])
                self.assertNotIn('model/list', [method for method, _ in native.calls])
                self.assertEqual(native.model, main_model)

    def test_fixed_title_model_rejection_does_not_try_a_different_model(self):
        generator = TitleGenerator()
        native = TitleRuntime(generator, provider='custom', model='main-model')
        native.failures['thread/start'] = AppServerError('Synthetic gpt-6-luna unavailable')
        with self.assertRaises(TitleError) as caught:
            generator.run(native.context, 'Fix the login timeout',
                          cancelled=threading.Event(), progress=native.progress)
        self.assertFalse(caught.exception.unknown)
        self.assertEqual([params['model'] for method, params in native.calls if method == 'thread/start'],
                         ['gpt-6-luna'])
        self.assertNotIn('turn/start', [method for method, _ in native.calls])
        self.assertNotIn('thread/name/set', [method for method, _ in native.calls])

    def test_refuses_a_different_native_route_or_permission_before_sending_the_title_prompt(self):
        for override in (
                {'thread': {'cwd': '/fixture/other'}},
                {'thread': {'modelProvider': 'other-provider'}},
                {'model': 'other-model'},
                {'sandbox': {'type': 'workspaceWrite'}},
                {'thread': {'ephemeral': False}},
                {'thread': {'id': 'source'}}):
            with self.subTest(override=override):
                generator = TitleGenerator()
                native = TitleRuntime(generator)
                native.start_overrides = override
                with self.assertRaises(TitleError):
                    generator.run(native.context, 'Fix the login timeout',
                                  cancelled=threading.Event(), progress=native.progress)
                self.assertNotIn('turn/start', [method for method, _ in native.calls])
                self.assertIsNone(native.name)
                self.assertFalse(any(params.get('threadId') == 'source' for method, params in native.calls
                                     if method in ('turn/interrupt', 'thread/unsubscribe')))
                if override.get('thread', {}).get('id') != 'source':
                    self.assertEqual([params for method, params in native.calls if method == 'thread/unsubscribe'],
                                     [{'threadId': 'temporary'}])
                    self.assertTrue(any(fields.get('temporary_thread_id') == 'temporary'
                                        for _, fields in native.stages))

    def test_preserves_and_verifies_a_custom_managed_permission_profile(self):
        for returned_profile in ('company-profile', 'other-profile'):
            with self.subTest(returned_profile=returned_profile):
                generator = TitleGenerator()
                native = TitleRuntime(generator)
                native.context = replace(native.context, permission_profile='company-profile')
                native.start_overrides = {'activePermissionProfile': {'id': returned_profile},
                                          'sandbox': {'type': 'workspaceWrite'}}
                if returned_profile == 'company-profile':
                    generator.run(native.context, 'Fix the login timeout',
                                  cancelled=threading.Event(), progress=native.progress)
                    self.assertEqual(native.name, 'Fix login timeout')
                else:
                    with self.assertRaises(TitleError):
                        generator.run(native.context, 'Fix the login timeout',
                                      cancelled=threading.Event(), progress=native.progress)
                    self.assertNotIn('turn/start', [method for method, _ in native.calls])
                start = next(params for method, params in native.calls if method == 'thread/start')
                self.assertEqual(start['permissions'], 'company-profile')
                self.assertNotIn('sandbox', start)
                self.assertNotIn('default_permissions', start['config'])

    def test_private_approval_is_rejected_and_only_the_temporary_task_is_interrupted(self):
        generator = TitleGenerator()
        native = TitleRuntime(generator)
        native.extra_events = [{'id': 71, 'method': 'item/commandExecution/requestApproval', 'params': {
            'threadId': 'temporary', 'turnId': 'title-turn', 'command': 'must never execute'}}]
        with self.assertRaises(TitleError):
            generator.run(native.context, 'Fix the login timeout',
                          cancelled=threading.Event(), progress=native.progress)
        self.assertIsNone(native.name)
        self.assertEqual(len(native.replies), 1)
        self.assertEqual(native.replies[0][0], 71)
        self.assertIsNone(native.replies[0][1])
        self.assertIsInstance(native.replies[0][2], dict)
        self.assertEqual([params for method, params in native.calls if method == 'turn/interrupt'],
                         [{'threadId': 'temporary', 'turnId': 'title-turn'}])
        self.assertFalse(generator.consume({'method': 'turn/started', 'params': {'threadId': 'source'}}, native.key))

    def test_native_empty_name_is_eligible_and_closed_source_is_skipped(self):
        for closed in (False, True):
            with self.subTest(closed=closed):
                generator = TitleGenerator()
                native = TitleRuntime(generator)
                native.name = ''
                if closed:
                    native.source_overrides = {'status': {'type': 'notLoaded'}}
                    with self.assertRaises(TitleCancelled):
                        generator.run(native.context, 'Fix the login timeout',
                                      cancelled=threading.Event(), progress=native.progress)
                    self.assertNotIn('thread/start', [method for method, _ in native.calls])
                else:
                    generator.run(native.context, 'Fix the login timeout',
                                  cancelled=threading.Event(), progress=native.progress)
                    self.assertEqual(native.name, 'Fix login timeout')

    def test_a_cancelled_receipt_stage_prevents_the_next_native_mutation(self):
        for stage in ('starting', 'generating', 'generated', 'applying'):
            with self.subTest(stage=stage):
                generator = TitleGenerator()
                native = TitleRuntime(generator)

                def progress(current, **fields):
                    native.progress(current, **fields)
                    return False if current == stage else True

                with self.assertRaises(TitleCancelled):
                    generator.run(native.context, 'Fix the login timeout',
                                  cancelled=threading.Event(), progress=progress)
                self.assertIsNone(native.name)
                if stage == 'starting':
                    self.assertNotIn('thread/start', [method for method, _ in native.calls])
                if stage == 'generating':
                    self.assertNotIn('turn/start', [method for method, _ in native.calls])

    def test_prompt_is_bounded_without_splitting_unicode_and_title_source_is_native_feature(self):
        generator = TitleGenerator()
        native = TitleRuntime(generator)
        generator.run(native.context, '修复登录超时🚀' * 400,
                      cancelled=threading.Event(), progress=native.progress)
        start = next(params for method, params in native.calls if method == 'thread/start')
        self.assertEqual(start['threadSource'], 'thread_title')
        turn = next(params for method, params in native.calls if method == 'turn/start')
        prompt = turn['input'][0]['text']
        self.assertLessEqual(len(prompt.encode('utf-8')), 960)
        self.assertIn('修复登录超时', prompt)
        self.assertNotIn('\ufffd', prompt)

    def test_only_a_bounded_final_model_response_can_become_the_title(self):
        for output, phase in (
                (' ' * 8192 + '{"title":"Fix login timeout"}', 'final_answer'),
                ('{"title":"Fix login timeout"}', 'commentary'),
                ('{"title":"Fix\\nlogin"}', 'final_answer'),
                ('{"title":"First","title":"Fix login timeout"}', 'final_answer'),
                ('{"title":"Fix\u2028login"}', 'final_answer'),
                ('\ud800', 'final_answer'),
                ('{"title":"Fix login timeout","extra":true}', 'final_answer'),
                ('{"title":"' + '长' * 37 + '"}', 'final_answer')):
            with self.subTest(phase=phase, output_length=len(output)):
                generator = TitleGenerator()
                native = TitleRuntime(generator)
                native.output, native.final_phase = output, phase
                with self.assertRaises(TitleError):
                    generator.run(native.context, 'Fix the login timeout',
                                  cancelled=threading.Event(), progress=native.progress)
                self.assertIsNone(native.name)

    def test_unknown_native_writes_keep_unknown_status_and_are_not_replayed(self):
        for method, error, unknown in (
                ('thread/start', AppServerUnknownResult('Synthetic lost start response'), True),
                ('turn/start', AppServerUnknownResult('Synthetic lost turn response'), True),
                ('turn/start', AppServerError('Synthetic explicit rejection'), False)):
            with self.subTest(method=method, unknown=unknown):
                generator = TitleGenerator()
                native = TitleRuntime(generator)
                native.failures[method] = error
                with self.assertRaises(TitleError) as caught:
                    generator.run(native.context, 'Fix the login timeout',
                                  cancelled=threading.Event(), progress=native.progress)
                self.assertEqual(caught.exception.unknown, unknown)
                self.assertEqual([call for call, _ in native.calls].count(method), 1)
                self.assertIsNone(native.name)
                cleanup = [params for call, params in native.calls if call == 'thread/unsubscribe']
                self.assertEqual(cleanup, [{'threadId': 'temporary'}] if method == 'turn/start' else [])

    def test_private_event_overflow_fails_without_breaking_the_native_reader(self):
        generator = TitleGenerator()
        native = TitleRuntime(generator)
        native.extra_events = [{'method': 'item/started', 'params': {
            'threadId': 'temporary', 'turnId': 'title-turn',
            'item': {'id': str(index), 'type': 'reasoning'}}} for index in range(70)]
        with self.assertRaises(TitleError):
            generator.run(native.context, 'Fix the login timeout',
                          cancelled=threading.Event(), progress=native.progress)
        self.assertIsNone(native.name)
        self.assertEqual([params for method, params in native.calls if method == 'turn/interrupt'],
                         [{'threadId': 'temporary', 'turnId': 'title-turn'}])

    def test_unverifiable_effective_mcp_configuration_never_starts_a_title_thread(self):
        for servers in ([], '', False, None):
            with self.subTest(servers=servers):
                generator = TitleGenerator()
                native = TitleRuntime(generator)
                native.config['mcp_servers'] = servers
                with self.assertRaises(TitleError):
                    generator.run(native.context, 'Fix the login timeout',
                                  cancelled=threading.Event(), progress=native.progress)
                self.assertNotIn('thread/start', [method for method, _ in native.calls])

    def test_cancel_disconnect_and_timeout_only_stop_the_private_title_turn(self):
        for ending in ('cancel', 'disconnect', 'timeout'):
            with self.subTest(ending=ending):
                generator = TitleGenerator()
                native = TitleRuntime(generator)
                native.delay_output = True
                cancellation = threading.Event()
                errors = []

                def run():
                    try:
                        generator.run(native.context, 'Fix the login timeout', cancelled=cancellation,
                                      progress=native.progress, timeout=.05 if ending == 'timeout' else 2)
                    except TitleError as error:
                        errors.append(error)

                worker = threading.Thread(target=run, daemon=True)
                worker.start()
                self.assertTrue(native.turn_accepted.wait(1))
                if ending == 'cancel':
                    cancellation.set()
                elif ending == 'disconnect':
                    generator.disconnect(native.key)
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], TitleCancelled if ending != 'timeout' else TitleError)
                self.assertIsNone(native.name)
                self.assertEqual([params for method, params in native.calls if method == 'turn/interrupt'],
                                 [{'threadId': 'temporary', 'turnId': 'title-turn'}])
                self.assertEqual([params for method, params in native.calls if method == 'thread/unsubscribe'],
                                 [{'threadId': 'temporary'}])

    def test_unexpected_tools_native_errors_and_closed_private_threads_never_apply_a_name(self):
        for event in (
                {'method': 'item/started', 'params': {'threadId': 'temporary', 'turnId': 'title-turn',
                    'item': {'id': 'tool', 'type': 'commandExecution'}}},
                {'method': 'thread/closed', 'params': {'threadId': 'temporary'}},
                {'method': 'thread/status/changed', 'params': {'threadId': 'temporary',
                    'status': {'type': 'notLoaded'}}},
                {'method': 'error', 'params': {'threadId': 'temporary', 'turnId': 'title-turn', 'willRetry': False}}):
            with self.subTest(method=event['method']):
                generator = TitleGenerator()
                native = TitleRuntime(generator)
                native.extra_events = [event]
                with self.assertRaises(TitleError):
                    generator.run(native.context, 'Fix the login timeout',
                                  cancelled=threading.Event(), progress=native.progress)
                self.assertIsNone(native.name)
                self.assertFalse(any(params.get('threadId') == 'source' for method, params in native.calls
                                     if method in ('turn/interrupt', 'thread/unsubscribe')))

    def test_lost_turn_start_response_still_rejects_its_private_question_and_cleans_known_turn(self):
        class LostResponseRuntime(TitleRuntime):
            def request(self, method, params, *, timeout):
                if method == 'turn/start':
                    self.calls.append((method, copy.deepcopy(params)))
                    self.generator.consume({'method': 'turn/started', 'params': {
                        'threadId': 'temporary', 'turn': {'id': 'title-turn', 'status': 'inProgress'}}}, self.key)
                    self.generator.consume({'id': 71, 'method': 'item/commandExecution/requestApproval',
                        'params': {'threadId': 'temporary', 'turnId': 'title-turn'}}, self.key)
                    raise AppServerUnknownResult('Synthetic lost turn/start response')
                return super().request(method, params, timeout=timeout)

        generator = TitleGenerator()
        native = LostResponseRuntime(generator)
        with self.assertRaises(TitleError) as caught:
            generator.run(native.context, 'Fix the login timeout',
                          cancelled=threading.Event(), progress=native.progress)
        self.assertTrue(caught.exception.unknown)
        self.assertIsNone(native.name)
        self.assertEqual([reply[0] for reply in native.replies], [71])
        self.assertEqual([params for method, params in native.calls if method == 'turn/interrupt'],
                         [{'threadId': 'temporary', 'turnId': 'title-turn'}])

    def test_revocation_as_turn_start_returns_retains_the_known_private_turn_for_cleanup(self):
        class RevokedRuntime(TitleRuntime):
            def request(self, method, params, *, timeout):
                if method == 'turn/start':
                    self.calls.append((method, copy.deepcopy(params)))
                    self.generator.disconnect(self.key)
                    return {'turn': {'id': 'title-turn', 'status': 'inProgress'}}
                return super().request(method, params, timeout=timeout)

        generator = TitleGenerator()
        native = RevokedRuntime(generator)
        with self.assertRaises(TitleCancelled):
            generator.run(native.context, 'Fix the login timeout',
                          cancelled=threading.Event(), progress=native.progress)
        self.assertIsNone(native.name)
        self.assertEqual([params for method, params in native.calls if method == 'turn/interrupt'],
                         [{'threadId': 'temporary', 'turnId': 'title-turn'}])


class TitleConnectionTests(unittest.TestCase):
    def test_revoked_source_guard_blocks_mutation_even_on_the_same_proxy_connection(self):
        processes = []

        def factory(*args, **kwargs):
            process = FixtureProcess(*args, **kwargs)
            processes.append(process)
            return process

        client = CodexAppServer('/fixture/existing.sock', check_socket=False, process_factory=factory)
        self.addCleanup(client.close)
        client.connect()
        original = (client.process, client.transport)
        with self.assertRaises(AppServerUnavailable):
            client.request('thread/start', {'ephemeral': True}, expected_connection=original,
                           current_guard=lambda: False)
        with self.assertRaises(AppServerUnavailable):
            client.respond(71, error={'code': -32000, 'message': 'Private request'},
                           expected_connection=original, current_guard=lambda: False)
        client.request('thread/read', {'threadId': 'source'}, replay_safe=True)
        self.assertFalse(any(message.get('method') == 'thread/start' or message.get('id') == 71
                             for message in processes[0].seen))

    def test_old_connection_cannot_start_a_title_after_proxy_reconnects(self):
        processes = []

        def factory(*args, **kwargs):
            process = FixtureProcess(*args, **kwargs)
            processes.append(process)
            return process

        client = CodexAppServer('/fixture/existing.sock', check_socket=False, process_factory=factory)
        self.addCleanup(client.close)
        client.connect()
        original = (client.process, client.transport)
        client.close()
        client.connect()
        with self.assertRaises(AppServerUnavailable):
            client.request('thread/start', {'ephemeral': True}, expected_connection=original)
        self.assertFalse(any(message.get('method') == 'thread/start' for message in processes[-1].seen))

    def test_private_shared_server_reply_checks_current_connection_before_sending(self):
        class Stream:
            def __init__(self):
                self.sent = []

            def sendall(self, data):
                self.sent.append(data)

        stream = Stream()
        server = SharedServer(stream)
        with self.assertRaises(IPCError):
            server.respond(71, error={'code': -32000, 'message': 'No title interaction'},
                           current_guard=lambda: False)
        self.assertEqual(stream.sent, [])


if __name__ == '__main__':
    unittest.main()
