"""Durable automatic-title jobs started only by an accepted explicit send."""
import copy
import json
import logging
import os
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


_IDENTITY_FIELDS = {'host', 'home', 'cwd', 'modelProvider', 'model', 'accountIdentity', 'origin'}
_REQUIRED_IDENTITY = {'host', 'home', 'cwd', 'modelProvider'}
_PROGRESS_FIELDS = {'title', 'tempThreadId', 'tempTurnId'}
_PROGRESS_NAMES = {'temporary_thread_id': 'tempThreadId', 'turn_id': 'tempTurnId'}
_STAGES = {'scheduled', 'starting', 'generating', 'generated', 'applying'}
_STATUSES = _STAGES | {'armed', 'confirmed', 'failed', 'cancelled', 'unknown'}
_RECEIPT_FIELDS = {'id', 'threadId', 'creationId', 'identity', 'status', 'submissionId',
                   'title', 'tempThreadId', 'tempTurnId', 'error'}
_LOGGER = logging.getLogger(__name__)


def title_input(text, files):
    """Use the original message, or attachment labels without paths or contents."""
    if text.strip():
        return text
    names = [file['name'] for file in files if isinstance(file.get('name'), str) and file['name']]
    return 'Attached files:\n' + '\n'.join(names) if names else ''


class AutomaticTitles:
    def __init__(self, directory, runner, *, max_workers=2):
        self.path = Path(directory) / 'auto-titles.json'
        self.runner = runner
        self.lock = threading.RLock()
        self.closed = False
        self.disabled = False
        try:
            self.entries = self._load()
        except (OSError, ValueError, TypeError) as error:
            self.entries, self.disabled = {}, True
            _LOGGER.warning('Automatic-title receipts unavailable (%s)', type(error).__name__)
        interrupted = False
        for row in self.entries.values():
            if row.get('status') in _STAGES:
                row.update(status='unknown', error='Interrupted')
                interrupted = True
        if interrupted:
            self.disabled = not self._save_quietly()
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix='auto-title')
        self.capacity = threading.BoundedSemaphore(max_workers * 4)
        self.cancellations = {}

    def _load(self):
        entries = json.loads(self.path.read_text('utf-8')) if self.path.exists() else {}
        if not isinstance(entries, dict):
            raise ValueError('Invalid automatic-title receipts')
        for thread_id, row in entries.items():
            if (not isinstance(row, dict) or row.get('threadId') != thread_id or
                    set(row) - _RECEIPT_FIELDS or row.get('status') not in _STATUSES):
                raise ValueError('Invalid automatic-title receipt')
            for name in ('id', 'threadId', 'creationId'):
                if not isinstance(row.get(name), str):
                    raise ValueError('Invalid automatic-title receipt identity')
                uuid.UUID(row[name])
            identity = row.get('identity')
            if (not isinstance(identity, dict) or set(identity) - _IDENTITY_FIELDS or
                    any(not isinstance(value, str) for value in identity.values()) or
                    any(not identity.get(name) for name in _REQUIRED_IDENTITY) or
                    any(not isinstance(value, str) for name, value in row.items() if name != 'identity')):
                raise ValueError('Invalid automatic-title receipt evidence')
            if (row['status'] in _STAGES and not row.get('submissionId') or
                    row['status'] == 'armed' and set(row) & {'submissionId', 'title', 'tempThreadId', 'tempTurnId'} or
                    row['status'] == 'confirmed' and not row.get('title')):
                raise ValueError('Invalid automatic-title receipt stage')
        return entries

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(prefix='.auto-titles-', dir=self.path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
                json.dump(self.entries, output, ensure_ascii=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _save_quietly(self):
        try:
            self._save()
            return True
        except OSError as error:
            _LOGGER.warning('Automatic-title receipt unavailable (%s)', type(error).__name__)
            return False

    def arm(self, thread_id, creation_id, identity):
        uuid.UUID(thread_id)
        uuid.UUID(creation_id)
        if not isinstance(identity, dict) or any(not isinstance(identity.get(key), str) or not identity[key]
                                                  for key in _REQUIRED_IDENTITY):
            raise ValueError('Automatic-title source identity is incomplete')
        identity = {key: value for key, value in identity.items()
                    if key in _IDENTITY_FIELDS and isinstance(value, str)}
        with self.lock:
            old = self.entries.get(thread_id)
            if old:
                if old['creationId'] != creation_id or old['identity'] != identity:
                    raise ValueError('Automatic-title creation identity changed')
                return copy.deepcopy(old)
            if self.closed or self.disabled:
                return None
            row = {'id': str(uuid.uuid4()), 'threadId': thread_id, 'creationId': creation_id,
                   'identity': identity, 'status': 'armed'}
            self.entries[thread_id] = row
            if not self._save_quietly():
                row.update(status='failed', error='PersistenceUnavailable')
                return None
            return copy.deepcopy(row)

    def accepted(self, thread_id, submission_id, text):
        try:
            uuid.UUID(submission_id)
        except (ValueError, TypeError, AttributeError):
            return False
        with self.lock:
            row = self.entries.get(thread_id)
            if self.closed or self.disabled or row is None or row['status'] != 'armed' or not isinstance(text, str) or not text.strip():
                return False
            if not self.capacity.acquire(blocking=False):
                row.update(status='failed', error='QueueFull', submissionId=submission_id)
                self._save_quietly()
                return False
            row.update(status='scheduled', submissionId=submission_id)
            if not self._save_quietly():
                row.update(status='failed', error='PersistenceUnavailable')
                self.capacity.release()
                return False
            cancelled = self.cancellations[thread_id] = threading.Event()
            try:
                future = self.executor.submit(self._run, copy.deepcopy(row), text, cancelled)
            except Exception as error:
                cancelled.set()
                self.cancellations.pop(thread_id, None)
                row.update(status='failed', error=type(error).__name__)
                self._save_quietly()
                self.capacity.release()
                return False
            future.add_done_callback(lambda value: self._done(thread_id, cancelled))
            return True

    def _run(self, job, text, cancelled):
        thread_id = job['threadId']

        def progress(stage, **fields):
            with self.lock:
                row = self.entries[thread_id]
                if cancelled.is_set() or row['id'] != job['id'] or row['status'] not in _STAGES:
                    return False
                if stage not in _STAGES:
                    raise ValueError('Unknown automatic-title stage')
                row['status'] = stage
                for name, value in fields.items():
                    key = _PROGRESS_NAMES.get(name, name)
                    if key in _PROGRESS_FIELDS and isinstance(value, str):
                        row[key] = value
                self._save()
                return True

        completed = False
        try:
            if cancelled.is_set():
                return
            title = self.runner(job, text, cancelled, progress)
            completed = True
            if not isinstance(title, str) or not title.strip():
                raise ValueError('Automatic title did not return a title')
            with self.lock:
                row = self.entries[thread_id]
                if not cancelled.is_set():
                    row.update(status='confirmed', title=title)
                    self._save()
        except Exception as error:
            with self.lock:
                row = self.entries[thread_id]
                if not cancelled.is_set():
                    state = ('cancelled' if getattr(error, 'cancelled', False) else
                             'unknown' if completed or getattr(error, 'unknown', False) else 'failed')
                    row.update(status=state,
                               error=type(error).__name__)
                    self._save_quietly()
            _LOGGER.info('Automatic title ended with %s', type(error).__name__)
        finally:
            self._finished(thread_id, cancelled)

    def _finished(self, thread_id, cancelled):
        with self.lock:
            if self.cancellations.get(thread_id) is cancelled:
                self.cancellations.pop(thread_id, None)

    def _done(self, thread_id, cancelled):
        self._finished(thread_id, cancelled)
        self.capacity.release()

    def status(self, thread_id):
        with self.lock:
            return copy.deepcopy(self.entries.get(thread_id))

    def has_pending(self):
        with self.lock:
            return bool(self.cancellations)

    def cancel(self, thread_id):
        with self.lock:
            row = self.entries.get(thread_id)
            cancelled = self.cancellations.get(thread_id)
            if cancelled is not None:
                cancelled.set()
            if row is None or row['status'] not in _STAGES | {'armed'}:
                return False
            row['status'] = 'cancelled'
            self._save_quietly()
            return True

    def observe_name(self, thread_id, name):
        """Native renames supersede this job; its own apply notification is an echo."""
        if name is not None and not isinstance(name, str):
            return False
        with self.lock:
            row = self.entries.get(thread_id)
            if row and row['status'] == 'applying' and row.get('title') == name:
                return False
            return self.cancel(thread_id)

    def cancel_all(self, *, include_armed=False):
        with self.lock:
            for cancelled in self.cancellations.values():
                cancelled.set()
            changed = False
            for row in self.entries.values():
                if row['status'] in _STAGES or include_armed and row['status'] == 'armed':
                    row['status'] = 'cancelled'
                    changed = True
            if changed:
                self._save_quietly()

    def close(self):
        with self.lock:
            self.closed = True
            self.cancel_all()
        self.executor.shutdown(wait=False, cancel_futures=True)
