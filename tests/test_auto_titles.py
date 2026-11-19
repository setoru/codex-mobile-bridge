"""Durable, asynchronous automatic title jobs through their public interface."""
import tempfile
import threading
import time
import unittest
import uuid
import subprocess
import sys
from pathlib import Path

from bridge.features.sessions.auto_titles import AutomaticTitles


ROOT = Path(__file__).resolve().parents[1]


class AutomaticTitleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp')
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.thread_id = str(uuid.uuid4())
        self.creation_id = str(uuid.uuid4())
        self.submission_id = str(uuid.uuid4())
        self.identity = {'host': 'local', 'home': '/fixture/.codex',
                         'cwd': '/fixture/project', 'modelProvider': 'fixture-provider'}

    def jobs(self, runner, **options):
        value = AutomaticTitles(self.directory, runner, **options)
        self.addCleanup(value.close)
        return value

    def wait_status(self, jobs, status):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            result = jobs.status(self.thread_id)
            if result and result['status'] == status:
                return result
            threading.Event().wait(.005)
        self.fail('Title job did not reach ' + status + ': ' + repr(jobs.status(self.thread_id)))

    def wait_idle(self, jobs):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not jobs.has_pending():
                return
            threading.Event().wait(.005)
        self.fail('Automatic title worker did not finish')

    def test_accepted_first_message_runs_once_and_confirms_generated_title(self):
        calls = []

        def runner(job, text, cancelled, progress):
            calls.append((job, text))
            progress('starting', temporary_thread_id='temporary-thread')
            progress('generating', turn_id='temporary-turn')
            progress('generated', title='Explain the project')
            progress('applying')
            return 'Explain the project'

        jobs = self.jobs(runner)
        jobs.arm(self.thread_id, self.creation_id, self.identity)
        self.assertTrue(jobs.accepted(self.thread_id, self.submission_id, 'Explain this repository'))
        result = self.wait_status(jobs, 'confirmed')
        self.assertEqual(result['title'], 'Explain the project')
        self.assertEqual(result['tempThreadId'], 'temporary-thread')
        self.assertEqual(result['tempTurnId'], 'temporary-turn')
        self.assertEqual(result['submissionId'], self.submission_id)
        self.assertEqual(calls[0][0]['identity'], self.identity)
        self.assertEqual(calls[0][1], 'Explain this repository')
        self.assertFalse(jobs.accepted(self.thread_id, self.submission_id, 'Explain this repository'))
        self.assertFalse(jobs.accepted(self.thread_id, str(uuid.uuid4()), 'Another prompt'))
        self.assertEqual(len(calls), 1)

    def test_cancelled_title_does_not_apply_late_generated_result(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def runner(job, text, cancelled, progress):
            progress('generating')
            started.set()
            release.wait(2)
            self.assertFalse(progress('generated', title='Late result'))
            self.assertTrue(cancelled.is_set())
            finished.set()
            return 'Late result'

        jobs = self.jobs(runner)
        jobs.arm(self.thread_id, self.creation_id, self.identity)
        self.assertTrue(jobs.accepted(self.thread_id, self.submission_id, 'First prompt'))
        self.assertTrue(started.wait(1))
        self.assertTrue(jobs.has_pending())
        jobs.cancel(self.thread_id)
        self.assertEqual(jobs.status(self.thread_id)['status'], 'cancelled')
        self.assertTrue(jobs.has_pending(), 'Cancelled workers remain active until cleanup finishes')
        release.set()
        self.assertTrue(finished.wait(1))
        self.wait_idle(jobs)
        self.assertEqual(jobs.status(self.thread_id)['status'], 'cancelled')
        self.assertFalse(jobs.accepted(self.thread_id, str(uuid.uuid4()), 'Try again'))

    def test_own_name_update_during_apply_keeps_successful_title(self):
        def runner(job, text, cancelled, progress):
            progress('generated', title='Generated native title')
            progress('applying')
            jobs.observe_name(self.thread_id, 'Generated native title')
            self.assertFalse(cancelled.is_set())
            return 'Generated native title'

        jobs = self.jobs(runner)
        jobs.arm(self.thread_id, self.creation_id, self.identity)
        jobs.accepted(self.thread_id, self.submission_id, 'Original prompt')
        self.assertEqual(self.wait_status(jobs, 'confirmed')['title'], 'Generated native title')

    def test_native_rename_before_first_send_cancels_title_eligibility(self):
        calls = []
        jobs = self.jobs(lambda *args: calls.append(args))
        jobs.arm(self.thread_id, self.creation_id, self.identity)
        jobs.observe_name(self.thread_id, 'Manually chosen title')
        self.assertEqual(jobs.status(self.thread_id)['status'], 'cancelled')
        self.assertFalse(jobs.accepted(self.thread_id, self.submission_id, 'Original prompt'))
        self.assertEqual(calls, [])

    def test_process_restart_keeps_inflight_results_unknown_without_replay(self):
        for stage in ('scheduled', 'starting', 'generating', 'generated', 'applying'):
            with self.subTest(stage=stage):
                directory = self.directory / stage
                armed_id = str(uuid.uuid4())
                source = f'''
import os
import threading
from bridge.features.sessions.auto_titles import AutomaticTitles

def runner(job, text, cancelled, progress):
    if {stage!r} == 'scheduled':
        os._exit(0)
    for name, fields in [('starting', {{'temporary_thread_id': 'known-temporary'}}),
                         ('generating', {{'turn_id': 'known-turn'}}),
                         ('generated', {{'title': 'Known candidate'}}), ('applying', {{}})]:
        progress(name, **fields)
        if name == {stage!r}:
            os._exit(0)

jobs = AutomaticTitles({str(directory)!r}, runner)
jobs.arm({self.thread_id!r}, {self.creation_id!r}, {self.identity!r})
jobs.arm({armed_id!r}, {str(uuid.uuid4())!r}, {self.identity!r})
jobs.accepted({self.thread_id!r}, {self.submission_id!r}, 'Private prompt')
threading.Event().wait(3)
raise RuntimeError('Synthetic crash did not happen')
'''
                crashed = subprocess.run([sys.executable, '-B', '-c', source], cwd=ROOT,
                                         capture_output=True, text=True, timeout=4)
                self.assertEqual(crashed.returncode, 0, crashed.stderr)
                calls = []
                restored = AutomaticTitles(directory, lambda *args: calls.append(args))
                self.addCleanup(restored.close)
                result = restored.status(self.thread_id)
                self.assertEqual(result['status'], 'unknown')
                self.assertFalse(restored.accepted(self.thread_id, self.submission_id, 'Private prompt'))
                self.assertFalse(restored.accepted(self.thread_id, str(uuid.uuid4()), 'New prompt'))
                self.assertEqual(restored.status(armed_id)['status'], 'armed')
                self.assertFalse(restored.has_pending())
                self.assertEqual(calls, [])
                again = AutomaticTitles(directory, lambda *args: calls.append(args))
                self.addCleanup(again.close)
                self.assertEqual(again.status(self.thread_id)['status'], 'unknown')

    def test_disconnect_cancels_worker_but_preserves_armed_chat_for_explicit_send(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def runner(job, text, cancelled, progress):
            progress('generating')
            started.set()
            release.wait(2)
            return 'Generated title'

        jobs = self.jobs(runner)
        jobs.arm(self.thread_id, self.creation_id, self.identity)
        other_id, other_creation = str(uuid.uuid4()), str(uuid.uuid4())
        jobs.arm(other_id, other_creation, self.identity)
        jobs.accepted(self.thread_id, self.submission_id, 'Original prompt')
        self.assertTrue(started.wait(1))
        jobs.cancel_all()
        self.assertEqual(jobs.status(self.thread_id)['status'], 'cancelled')
        self.assertEqual(jobs.status(other_id)['status'], 'armed')
        release.set()
        self.wait_idle(jobs)
        jobs.close()
        self.assertFalse(jobs.accepted(other_id, str(uuid.uuid4()), 'Explicit first prompt'))
        restored = self.jobs(lambda *args: 'New title')
        self.assertEqual(restored.arm(other_id, other_creation, self.identity)['status'], 'armed')
        self.assertTrue(restored.accepted(other_id, str(uuid.uuid4()), 'Explicit first prompt'))
        self.thread_id = other_id
        self.wait_status(restored, 'confirmed')

    def test_cancel_all_can_permanently_revoke_armed_chats(self):
        jobs = self.jobs(lambda *args: 'Title')
        jobs.arm(self.thread_id, self.creation_id, self.identity)
        jobs.cancel_all(include_armed=True)
        self.assertEqual(jobs.status(self.thread_id)['status'], 'cancelled')
        self.assertFalse(jobs.accepted(self.thread_id, self.submission_id, 'First prompt'))

    def test_close_cancels_queued_jobs_and_waits_only_for_active_cleanup_in_audit(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def runner(job, text, cancelled, progress):
            started.set()
            release.wait(2)
            return 'Title'

        jobs = self.jobs(runner, max_workers=1)
        other_id = str(uuid.uuid4())
        jobs.arm(self.thread_id, self.creation_id, self.identity)
        jobs.arm(other_id, str(uuid.uuid4()), self.identity)
        jobs.accepted(self.thread_id, self.submission_id, 'First prompt')
        self.assertTrue(started.wait(1))
        jobs.accepted(other_id, str(uuid.uuid4()), 'Another first prompt')
        jobs.close()
        self.assertEqual(jobs.status(self.thread_id)['status'], 'cancelled')
        self.assertEqual(jobs.status(other_id)['status'], 'cancelled')
        self.assertTrue(jobs.has_pending())
        release.set()
        self.wait_idle(jobs)

    def test_persistence_failure_never_dispatches_title_or_escapes_accepted_send(self):
        calls = []
        jobs = self.jobs(lambda *args: calls.append(args))
        jobs.arm(self.thread_id, self.creation_id, self.identity)
        jobs.path.unlink()
        jobs.path.mkdir()
        self.assertFalse(jobs.accepted(self.thread_id, self.submission_id, 'Private prompt'))
        self.assertEqual(jobs.status(self.thread_id)['status'], 'failed')
        self.assertEqual(calls, [])
        self.assertFalse(jobs.has_pending())

    def test_failed_or_unknown_model_result_is_terminal_and_not_retried(self):
        class NativeFailure(RuntimeError):
            unknown = False
            cancelled = False

        for state in ('failed', 'unknown', 'cancelled'):
            with self.subTest(state=state):
                self.thread_id = str(uuid.uuid4())
                calls = []
                error = NativeFailure('Synthetic private token must never be recorded')
                error.unknown, error.cancelled = state == 'unknown', state == 'cancelled'

                def runner(*args):
                    calls.append(args)
                    raise error

                jobs = self.jobs(runner)
                jobs.arm(self.thread_id, str(uuid.uuid4()), self.identity)
                self.assertTrue(jobs.accepted(self.thread_id, str(uuid.uuid4()), 'Private prompt'))
                self.wait_status(jobs, state)
                self.assertFalse(jobs.accepted(self.thread_id, str(uuid.uuid4()), 'Private prompt'))
                self.assertEqual(len(calls), 1)
                self.assertNotIn('private token', jobs.path.read_text('utf-8'))
                jobs.close()

    def test_full_background_queue_is_rejected_without_blocking_or_later_retry(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls = []

        def runner(job, text, cancelled, progress):
            calls.append(job['threadId'])
            started.set()
            release.wait(2)
            return 'Title'

        jobs = self.jobs(runner, max_workers=1)
        jobs.arm(self.thread_id, self.creation_id, self.identity)
        self.assertTrue(jobs.accepted(self.thread_id, self.submission_id, 'First prompt'))
        self.assertTrue(started.wait(1))
        for _ in range(3):
            identifier = str(uuid.uuid4())
            jobs.arm(identifier, str(uuid.uuid4()), self.identity)
            self.assertTrue(jobs.accepted(identifier, str(uuid.uuid4()), 'Queued first prompt'))
        excess = str(uuid.uuid4())
        jobs.arm(excess, str(uuid.uuid4()), self.identity)
        self.assertFalse(jobs.accepted(excess, str(uuid.uuid4()), 'Excess first prompt'))
        self.assertEqual(jobs.status(excess)['status'], 'failed')
        self.assertFalse(jobs.accepted(excess, str(uuid.uuid4()), 'Try again'))
        jobs.cancel_all()
        release.set()
        self.wait_idle(jobs)
        self.assertEqual(calls, [self.thread_id])

    def test_creation_identity_and_snapshots_cannot_be_repurposed(self):
        jobs = self.jobs(lambda *args: 'Title')
        original = jobs.arm(self.thread_id, self.creation_id, self.identity)
        self.assertEqual(jobs.arm(self.thread_id, self.creation_id, self.identity)['id'], original['id'])
        original['identity']['modelProvider'] = 'different'
        self.assertEqual(jobs.status(self.thread_id)['identity'], self.identity)
        for creation, identity in [(str(uuid.uuid4()), self.identity),
                                   (self.creation_id, {**self.identity, 'home': '/different/.codex'})]:
            with self.subTest(creation=creation), self.assertRaises(ValueError):
                jobs.arm(self.thread_id, creation, identity)
        for identity in ({}, {**self.identity, 'home': None}, {**self.identity, 'modelProvider': ''}):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                jobs.arm(str(uuid.uuid4()), str(uuid.uuid4()), identity)

    def test_credentials_prompt_and_worker_bindings_are_never_persisted(self):
        def runner(job, text, cancelled, progress):
            job['_binding'] = object()
            progress('generated', title='Generated title', _binding=object(), text=text)
            progress('applying')
            return 'Generated title'

        jobs = self.jobs(runner)
        identity = {**self.identity, 'accountIdentity': 'fixture-fingerprint',
                    'apiKey': 'synthetic-secret', 'auth': {'token': 'synthetic-secret'}}
        jobs.arm(self.thread_id, self.creation_id, identity)
        jobs.accepted(self.thread_id, self.submission_id, 'Private original user prompt')
        self.wait_status(jobs, 'confirmed')
        content = jobs.path.read_text('utf-8')
        self.assertNotIn('Private original user prompt', content)
        self.assertNotIn('synthetic-secret', content)
        self.assertNotIn('_binding', content)
        self.assertEqual(jobs.status(self.thread_id)['identity'],
                         {**self.identity, 'accountIdentity': 'fixture-fingerprint'})

    def test_late_progress_cannot_reopen_a_terminal_title_job(self):
        callbacks = []

        def runner(job, text, cancelled, progress):
            callbacks.append(progress)
            return 'Confirmed title'

        jobs = self.jobs(runner)
        jobs.arm(self.thread_id, self.creation_id, self.identity)
        jobs.accepted(self.thread_id, self.submission_id, 'First prompt')
        self.wait_status(jobs, 'confirmed')
        self.assertFalse(callbacks[0]('generated', title='Late candidate'))
        self.assertEqual(jobs.status(self.thread_id)['status'], 'confirmed')
        self.assertEqual(jobs.status(self.thread_id)['title'], 'Confirmed title')

    def test_invalid_receipts_disable_naming_without_overwriting_evidence(self):
        for index, content in enumerate(('not JSON', '[]', '{"invalid-thread":[]}')):
            with self.subTest(content=content):
                directory = self.directory / str(index)
                directory.mkdir()
                path = directory / 'auto-titles.json'
                path.write_text(content, encoding='utf-8')
                calls = []
                jobs = AutomaticTitles(directory, lambda *args: calls.append(args))
                self.addCleanup(jobs.close)
                self.assertIsNone(jobs.arm(self.thread_id, self.creation_id, self.identity))
                self.assertFalse(jobs.accepted(self.thread_id, self.submission_id, 'First prompt'))
                self.assertFalse(jobs.has_pending())
                self.assertEqual(path.read_text('utf-8'), content)
                self.assertEqual(calls, [])

    def test_concurrent_accepted_sends_reserve_exactly_one_title_job(self):
        ready, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        calls, accepted = [], []
        barrier = threading.Barrier(12)

        def runner(job, text, cancelled, progress):
            calls.append(job['submissionId'])
            ready.set()
            release.wait(2)
            return 'Title'

        jobs = self.jobs(runner)
        jobs.arm(self.thread_id, self.creation_id, self.identity)

        def submit():
            barrier.wait(timeout=2)
            accepted.append(jobs.accepted(self.thread_id, str(uuid.uuid4()), 'First prompt'))

        callers = [threading.Thread(target=submit) for _ in range(12)]
        for caller in callers:
            caller.start()
        for caller in callers:
            caller.join(timeout=2)
            self.assertFalse(caller.is_alive())
        self.assertTrue(ready.wait(1))
        self.assertEqual(accepted.count(True), 1)
        self.assertEqual(accepted.count(False), 11)
        self.assertEqual(calls, [jobs.status(self.thread_id)['submissionId']])
        release.set()
        self.wait_status(jobs, 'confirmed')

    def test_native_name_cancellation_survives_receipt_storage_failure(self):
        ready, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)

        def runner(job, text, cancelled, progress):
            progress('generating')
            ready.set()
            release.wait(2)
            self.assertTrue(cancelled.is_set())
            self.assertFalse(progress('applying'))
            return 'Late title'

        jobs = self.jobs(runner)
        jobs.arm(self.thread_id, self.creation_id, self.identity)
        jobs.accepted(self.thread_id, self.submission_id, 'First prompt')
        self.assertTrue(ready.wait(1))
        jobs.path.unlink()
        jobs.path.mkdir()
        jobs.observe_name(self.thread_id, 'Chosen title')
        self.assertEqual(jobs.status(self.thread_id)['status'], 'cancelled')
        release.set()
        self.wait_idle(jobs)
        self.assertEqual(jobs.status(self.thread_id)['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
