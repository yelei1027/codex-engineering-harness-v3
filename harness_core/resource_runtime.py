"""In-process service lifecycle for the single owned gateway.

Backend callbacks must enforce resource ownership and bounded startup themselves.
No container or filesystem access occurs in this module.
"""
import threading
import time
from contextlib import contextmanager

from .common import HarnessError


class ServiceRuntime:
    def __init__(self, start, stop, *, idle_seconds=120, startup_seconds=60,
                 clock=time.monotonic, manual=False, persist_manual=None):
        if idle_seconds <= 0 or startup_seconds <= 0:
            raise ValueError('Service deadlines must be positive')
        self._start = start
        self._stop = stop
        self._clock = clock
        self.idle_seconds = idle_seconds
        self.startup_seconds = startup_seconds
        self._condition = threading.Condition()
        self._state = 'DORMANT'
        self._active = 0
        self._pinned = manual
        self._persist_manual = persist_manual or (lambda enabled: None)
        self._idle_since = None
        self._failure = None
        self._generation = 0
        self._accepting = True
        self._cleanup_pending = False

    def status(self):
        with self._condition:
            deadline = None
            if self._state == 'RUNNING' and not self._active and not self._pinned:
                deadline = self._idle_since + self.idle_seconds
            return {'state': self._state, 'active_calls': self._active, 'accepting_calls':self._accepting,
                    'mode': 'MANUAL' if self._pinned else 'ON_DEMAND',
                    'idle_remaining_seconds': None if deadline is None else
                    max(0, deadline - self._clock()), 'failure': self._failure}

    def _acquire(self, manual=False):
        with self._condition:
            if not self._accepting:
                raise HarnessError('Resource is reserved for maintenance')
            generation = self._generation
            waited = False
            while self._state in ('STARTING', 'STOPPING'):
                waited = True
                self._condition.wait()
                if not self._accepting:
                    raise HarnessError('Resource is reserved for maintenance')
            if waited and self._generation != generation and self._state == 'START_FAILED':
                raise HarnessError('Service startup failed; inspect resource status')
            if self._state == 'RUNNING':
                self._active += 1
                self._pinned = self._pinned or manual
                self._idle_since = None
                return
            self._state = 'STARTING'
            self._failure = None
        try:
            self._start(self.startup_seconds)
        except BaseException:
            with self._condition:
                self._state = 'START_FAILED'
                self._cleanup_pending = True
                self._failure = 'START_FAILED'  # Never expose backend credentials/errors.
                self._generation += 1
                self._condition.notify_all()
            raise
        with self._condition:
            self._state = 'RUNNING'
            self._generation += 1
            self._active += 1
            self._pinned = self._pinned or manual
            self._idle_since = None
            self._condition.notify_all()

    def _release(self):
        with self._condition:
            self._active -= 1
            if not self._active:
                self._idle_since = self._clock()

    @contextmanager
    def use(self):
        self._acquire()
        try:
            yield
        finally:
            self._release()

    def quiesce(self):
        with self._condition:
            if self._active or self._state in ('STARTING', 'STOPPING'):
                raise HarnessError('Resource is in use; maintenance refused')
            self._accepting = False

    def resume(self):
        with self._condition:
            self._accepting = True
            self._condition.notify_all()

    def start_manual(self):
        with self._condition:
            if not self._accepting:
                raise HarnessError('Resource is reserved for maintenance')
            self._persist_manual(True)
            self._pinned = True
        self._acquire(manual=True)
        self._release()

    def stop(self, *, idle_only=False):
        with self._condition:
            if self._state in ('STARTING', 'STOPPING') or self._active:
                if idle_only:
                    return False
                raise HarnessError('Service is in use; stop refused')
            if self._state == 'DORMANT':
                if not idle_only:
                    self._persist_manual(False)
                self._pinned = False
                return False
            failed_cleanup=self._state=='START_FAILED' and self._cleanup_pending
            if idle_only and not failed_cleanup and (self._state != 'RUNNING' or self._pinned or
                              self._clock() - self._idle_since < self.idle_seconds):
                return False
            previous = self._state
            previous_pin = self._pinned
            if not idle_only:
                self._persist_manual(False)
            self._state = 'STOPPING'
        try:
            self._stop()
        except BaseException:
            with self._condition:
                self._state = previous
                self._failure = 'STOP_FAILED'
                try:
                    if previous_pin:
                        self._persist_manual(True)
                finally:
                    self._condition.notify_all()
            raise
        with self._condition:
            self._state = 'START_FAILED' if idle_only and failed_cleanup else 'DORMANT'
            self._pinned = previous_pin if idle_only and failed_cleanup else False
            self._idle_since = None
            self._cleanup_pending = False
            self._failure = 'START_FAILED' if idle_only and failed_cleanup else None
            self._condition.notify_all()
        return True
