"""FIFO ownership of a Prime turn, including its durable settlement."""
from collections import deque
import threading


class PrimeTurnQueue:
    def __init__(self):
        self._condition = threading.Condition()
        self._waiting = deque()
        self._owner = None

    def reserve(self):
        """Reserve arrival order before dispatching a background thread."""
        with self._condition:
            ticket = object()
            self._waiting.append(ticket)
            return ticket

    def acquire_reserved(self, ticket, blocking=True):
        with self._condition:
            if ticket not in self._waiting:
                raise RuntimeError("Prime turn reservation no longer exists")
            if not blocking and (self._owner is not None or self._waiting[0] is not ticket):
                return False
            while self._owner is not None or self._waiting[0] is not ticket:
                self._condition.wait()
            self._waiting.popleft()
            self._owner = ticket
            return True

    def cancel(self, ticket):
        with self._condition:
            if ticket in self._waiting:
                self._waiting.remove(ticket)
                self._condition.notify_all()

    def acquire(self, blocking=True):
        with self._condition:
            if not blocking:
                if self._owner is not None or self._waiting:
                    return False
                self._owner = object()
                return True
            ticket = self.reserve()
        return self.acquire_reserved(ticket)

    def release(self):
        with self._condition:
            if self._owner is None:
                raise RuntimeError("Prime turn queue is not owned")
            self._owner = None
            self._condition.notify_all()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
