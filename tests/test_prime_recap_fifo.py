"""Simultaneous results retain arrival order through provider and persistence."""
from types import SimpleNamespace
from unittest.mock import MagicMock
import threading

import pytest

from api import delegation_store, prime_brief_queue as pbq, prime_session_store as pss, routes
from api.prime_turn_queue import PrimeTurnQueue
from tests.test_prime_transport_continuity import Handler


@pytest.fixture
def recap(monkeypatch, tmp_path):
    store = pss.PrimeSessionStore(tmp_path / 'prime.json')
    queue = pbq.PrimeBriefQueue(tmp_path)
    monkeypatch.setattr(routes, 'DEFAULT_WORKSPACE', tmp_path)
    monkeypatch.setattr(pss, 'get_prime_session_store', lambda _: store)
    monkeypatch.setattr(pbq, 'get_brief_queue', lambda _: queue)
    monkeypatch.setattr(delegation_store, 'get_delegation_store', lambda _: MagicMock())
    monkeypatch.setattr('api.prime_delegation.get_background_task', lambda tid: {
        'id': tid, 'status': 'parziale', 'task': tid, 'output': 'artifact'})
    monkeypatch.setattr(routes, 'j', lambda _, payload, **kw: payload)
    return store, queue


def test_results_are_reserved_at_post_even_if_threads_start_backwards(recap, monkeypatch):
    store, _ = recap
    captured = []

    class DeferredWorker:
        def __init__(self, **kw):
            self.kw = kw
            captured.append(self)
        def start(self):
            pass
        def is_alive(self):
            return True

    monkeypatch.setattr(routes, 'threading', SimpleNamespace(Thread=DeferredWorker))
    calls = []
    monkeypatch.setattr(routes, '_hermes_prime_reply', lambda msg, *a, **kw:
                        calls.append(msg.split('Task: ')[1].split('\n')[0]) or {'reply': calls[-1]})
    turn_queue = routes._prime_bridge_turn_lock('hermes-prime')
    with turn_queue:
        for tid in ('d663', 'd664', 'd665'):
            response = routes._handle_bridge_prime_brief(object(), {'task_id': tid})
            assert response['state'] == 'queued'
        # Scheduling does not determine ownership: the last worker starts first.
        workers = [threading.Thread(target=w.kw['target'], args=w.kw['args']) for w in reversed(captured)]
        for w in workers:
            w.start()
        assert calls == []
    for w in workers:
        w.join(3)
        assert not w.is_alive()
    assert calls == ['d663', 'd664', 'd665']
    assert [m['task_id'] for m in store.history()['messages']] == calls
    assert all(routes._brief_job_get(tid)['state'] == 'done' for tid in calls)


def test_user_turn_cannot_jump_a_reserved_recap():
    gate = PrimeTurnQueue()
    gate.acquire()
    first, user, last = [gate.reserve() for _ in range(3)]
    calls = []
    def run(ticket, name):
        gate.acquire_reserved(ticket)
        try:
            calls.append(name)
        finally:
            gate.release()
    workers = [threading.Thread(target=run, args=(t, n)) for t, n in
               ((last, 'recap-2'), (user, 'user'), (first, 'recap-1'))]
    for w in workers:
        w.start()
    assert not gate.acquire(blocking=False)
    gate.release()
    for w in workers:
        w.join(3)
        assert not w.is_alive()
    assert calls == ['recap-1', 'user', 'recap-2']


def test_worker_exception_releases_queue_and_leaves_explicit_retry(recap, monkeypatch):
    store, queue = recap
    for tid in ('bad', 'good'):
        queue.enqueue(tid, 'programmatore', 'codice', tid, 'done', 'artifact')
    original = routes._run_prime_brief_job_owned
    def fail_once(tid, *args):
        if tid == 'bad':
            raise RuntimeError('unexpected worker error')
        return original(tid, *args)
    monkeypatch.setattr(routes, '_run_prime_brief_job_owned', fail_once)
    monkeypatch.setattr(routes, '_hermes_prime_reply', lambda *a, **kw: {'reply': 'good'})
    assert routes._run_prime_brief_job('bad', 'brief-bad', 'msg', queue._ws) == ''
    assert routes._brief_job_get('bad')['state'] == 'failed_retryable'
    routes._run_prime_brief_job('good', 'brief-good', 'msg', queue._ws)
    assert store.brief_delivery('brief-good')['reply'] == 'good'


def test_thread_start_failure_cancels_reservation(recap, monkeypatch):
    worker = MagicMock()
    worker.return_value.start.side_effect = RuntimeError('thread unavailable')
    monkeypatch.setattr(routes, 'threading', SimpleNamespace(Thread=worker))
    payload = routes._handle_bridge_prime_brief(object(), {'task_id': 'bad'})
    assert payload['state'] == 'failed_retryable'
    gate = routes._prime_bridge_turn_lock('hermes-prime')
    assert gate.acquire(blocking=False)
    gate.release()


def test_dead_worker_reports_recovery_and_durable_delivery_still_wins(recap):
    store, _ = recap
    routes._brief_job_set('bad', state='running', brief_id='brief-bad')
    routes._BRIEF_WORKERS['bad'] = SimpleNamespace(is_alive=lambda: False)
    parsed = SimpleNamespace(query='task_id=bad')
    payload = routes._handle_bridge_prime_brief_status(object(), parsed)
    assert payload['state'] == 'pending_recovery' and not payload['pending']
    store.inject_assistant_message('saved', {'brief_id': 'brief-bad'})
    payload = routes._handle_bridge_prime_brief_status(object(), parsed)
    assert payload['state'] == 'done' and payload['reply'] == 'saved'


def test_dead_queued_worker_cancels_ticket_before_retry(recap):
    gate = routes._prime_bridge_turn_lock('hermes-prime')
    ticket = gate.reserve()
    routes._BRIEF_RESERVATIONS['dead'] = (gate, ticket)
    routes._BRIEF_WORKERS['dead'] = SimpleNamespace(is_alive=lambda: False)
    routes._brief_job_set('dead', state='queued')
    assert routes._brief_job_get('dead')['state'] == 'pending_recovery'
    assert gate.acquire(blocking=False)
    gate.release()


def test_next_provider_waits_until_previous_recap_is_persisted(recap, monkeypatch):
    store, queue = recap
    gate = routes._prime_bridge_turn_lock('hermes-prime')
    first_ticket, second_ticket = gate.reserve(), gate.reserve()
    saving, release_save, second_called = threading.Event(), threading.Event(), threading.Event()
    inject = store.inject_assistant_message
    for tid in ('first', 'second'):
        queue.enqueue(tid, 'programmatore', 'codice', tid, 'done', 'artifact')
    def slow_save(reply, meta):
        if meta['task_id'] == 'first':
            saving.set()
            assert release_save.wait(3)
        return inject(reply, meta)
    def provider(message, *a, **kw):
        if message == 'second':
            second_called.set()
        return {'reply': message}
    monkeypatch.setattr(store, 'inject_assistant_message', slow_save)
    monkeypatch.setattr(routes, '_hermes_prime_reply', provider)
    workers = [threading.Thread(target=routes._run_prime_brief_job,
               args=(tid, 'brief-'+tid, tid, queue._ws, 'hermes-prime', 'giorgio', ticket))
               for tid, ticket in (('first', first_ticket), ('second', second_ticket))]
    try:
        for w in workers:
            w.start()
        assert saving.wait(1)
        assert not second_called.is_set()
        assert store.history()['messages'] == []
    finally:
        release_save.set()
        for w in workers:
            w.join(3)
            assert not w.is_alive()
    assert [m['task_id'] for m in store.history()['messages']] == ['first', 'second']


def test_user_completion_drain_cannot_steal_a_reserved_recap(recap, monkeypatch):
    store, queue = recap
    captured = []
    real_thread = threading.Thread
    class DeferredWorker:
        def __init__(self, **kw):
            self.kw = kw
            captured.append(self)
        def start(self):
            pass
        def is_alive(self):
            return True
    def dispatch_thread(**kw):
        return DeferredWorker(**kw) if kw.get('name', '').startswith('prime-brief-') else real_thread(**kw)
    monkeypatch.setattr(routes, 'threading', SimpleNamespace(Thread=dispatch_thread, Event=threading.Event, Lock=threading.Lock))
    monkeypatch.setattr(routes, '_sse_set_write_deadline', lambda _: None)
    monkeypatch.setattr(routes, '_bridge_prime_start_attention_relays', lambda *a: lambda: None)
    started, release = threading.Event(), threading.Event()
    def provider(*a, **kw):
        started.set()
        assert release.wait(3)
        return {'reply': 'user answer'}
    monkeypatch.setattr(routes, '_call_hermes_prime_reply_for_bridge', provider)
    monkeypatch.setattr(routes, '_hermes_prime_reply', lambda *a, **kw: {'reply': 'model recap'})
    user = real_thread(target=routes._handle_bridge_prime, args=(Handler(), {'message': 'question'}))
    try:
        user.start()
        assert started.wait(1)
        payload = routes._handle_bridge_prime_brief(object(), {'task_id': 'd663'})
        assert payload['state'] == 'queued'
        # An offline result still uses normal bounded recovery.
        queue.enqueue('offline', 'programmatore', 'codice', 'offline', 'done', 'artifact')
    finally:
        release.set()
        user.join(3)
        assert not user.is_alive()
    assert queue.is_delivered('brief-offline')
    assert not queue.is_delivered('brief-d663')
    assert store.brief_delivery('brief-d663') is None
    captured[0].kw['target'](*captured[0].kw['args'])
    assert store.brief_delivery('brief-d663')['reply'] == 'model recap'
    assert [m.get('content') for m in store.history()['messages']][:2] == ['question', 'user answer']
