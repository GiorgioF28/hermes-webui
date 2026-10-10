"""Recap queue time, durable recovery and simultaneous browser retries."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import MagicMock
import threading

import pytest

from api import prime_brief_queue as pbq, prime_session_store as pss, routes


@pytest.fixture
def delivery(monkeypatch, tmp_path):
    store = pss.PrimeSessionStore(tmp_path / 'prime.json')
    queue = pbq.PrimeBriefQueue(tmp_path)
    payloads = []
    monkeypatch.setattr(routes, '_BRIEF_JOBS', {})
    monkeypatch.setattr(routes, 'DEFAULT_WORKSPACE', tmp_path)
    monkeypatch.setattr(routes, '_request_prime_session_id', lambda _: 'hermes-prime')
    monkeypatch.setattr(pss, 'get_prime_session_store', lambda _: store)
    monkeypatch.setattr(pbq, 'get_brief_queue', lambda _: queue)
    monkeypatch.setattr(routes, 'j', lambda _, p, **kw: payloads.append((p, kw)) or True)
    return store, queue, payloads


def status(task='d1'):
    routes._handle_bridge_prime_brief_status(object(), SimpleNamespace(query='task_id=' + task))


def test_transcript_wins_over_stale_failure_and_missing_queue_ack(delivery):
    store, queue, payloads = delivery
    bid = queue.enqueue('d1', 'programmatore', 'codice', 'fix', 'done', 'fatto')
    queue.mark_failed(bid, 'previous persistence error')
    store.inject_assistant_message('Recap salvato', {'brief_id': bid, 'usage': {'input_tokens': 7}})
    routes._brief_job_set('d1', state='failed_retryable', brief_id=bid)
    status()
    assert payloads[-1][0]['state'] == 'done'
    assert payloads[-1][0]['reply'] == 'Recap salvato'
    original_time = store.brief_delivery(bid)['created_at']
    assert payloads[-1][0]['created_at'] == original_time
    routes._BRIEF_JOBS.clear()  # process restart
    status()
    assert payloads[-1][0]['delivered']
    assert payloads[-1][0]['created_at'] == original_time
    assert payloads[-1][0]['usage'] == {'input_tokens': 7}


def test_queue_metadata_survives_terminal_patch_and_session_guard(delivery):
    _, queue, payloads = delivery
    bid = queue.enqueue('d1', 'programmatore', 'codice', 'fix', 'done', 'fatto', session_id='other')
    queue.mark_delivered(bid)
    assert pbq.PrimeBriefQueue(queue._ws).get_brief(bid)['session_id'] == 'other'
    status()
    assert payloads[-1][1]['status'] == 404


def test_restart_pending_and_real_failure_are_distinct(delivery):
    _, queue, payloads = delivery
    bid = queue.enqueue('d1', 'programmatore', 'codice', 'fix', 'done', 'fatto')
    status()
    assert payloads[-1][0]['state'] == 'pending_recovery'
    assert not payloads[-1][0]['pending']  # no worker after restart
    queue.mark_failed(bid, 'write failed')
    status()
    assert payloads[-1][0]['state'] == 'failed_retryable'
    status('absent')
    assert payloads[-1][0]['state'] == 'unknown'


def test_queued_job_waits_for_lock_before_starting(delivery, monkeypatch):
    _, _, payloads = delivery
    entered = threading.Event()
    calls = []
    monkeypatch.setattr(routes, '_run_prime_brief_job_owned', lambda *a: entered.set() or calls.append(a))
    routes._brief_job_set('d1', state='queued', queued_at=1, started_at=None)
    lock = routes._prime_bridge_turn_lock('hermes-prime')
    with lock:
        worker = threading.Thread(target=routes._run_prime_brief_job,
                                  args=('d1', 'brief-d1', 'msg', '.'))
        worker.start()
        status()
        assert payloads[-1][0]['state'] == 'queued'
        assert payloads[-1][0]['pending']
        assert payloads[-1][0]['started_at'] is None
        assert not entered.is_set()
    worker.join(3)
    assert not worker.is_alive()
    assert calls
    assert routes._brief_job_get('d1')['started_at'] > 1


def test_simultaneous_retry_reserves_one_worker(delivery, monkeypatch):
    _, queue, _ = delivery
    monkeypatch.setattr('api.prime_delegation.get_background_task', lambda _: {
        'id': 'd1', 'status': 'ok', 'task': 'fix', 'output': 'fatto'})
    barrier = threading.Barrier(2)
    enqueue = queue.enqueue

    def synchronized_enqueue(*a, **kw):
        bid = enqueue(*a, **kw)
        barrier.wait(timeout=3)
        return bid

    monkeypatch.setattr(queue, 'enqueue', synchronized_enqueue)
    worker = MagicMock()
    monkeypatch.setattr(routes, 'threading', SimpleNamespace(Thread=worker))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: routes._handle_bridge_prime_brief(object(), {'task_id': 'd1'}), range(2)))
    worker.assert_called_once()
    worker.return_value.start.assert_called_once()


def test_retry_does_not_regenerate_transcript_without_queue_ack(delivery, monkeypatch):
    store, _, payloads = delivery
    store.inject_assistant_message('Unico recap', {'brief_id': 'brief-d1'})
    monkeypatch.setattr('api.prime_delegation.get_background_task', lambda _: {'id': 'd1', 'status': 'ok'})
    worker = MagicMock()
    monkeypatch.setattr(routes, 'threading', SimpleNamespace(Thread=worker))
    routes._handle_bridge_prime_brief(object(), {'task_id': 'd1'})
    assert payloads[-1][0]['already_delivered']
    worker.assert_not_called()


def test_reopened_and_archived_transcript_prove_delivery(delivery, monkeypatch):
    store, _, payloads = delivery
    store.inject_assistant_message('Storico', {'brief_id': 'brief-d1'})
    reopened = pss.PrimeSessionStore(store.path)
    monkeypatch.setattr(pss, 'get_prime_session_store', lambda _: reopened)
    status()
    assert payloads[-1][0]['reply'] == 'Storico'
    # Archive slots retain identity but must not rehydrate the old prose.
    with reopened._lock:
        data = reopened._read_locked()
        data['messages'] = [{'_prime_archive': {'index': 0}}]
        data['_prime_archive'] = {'brief_indexes': {'brief-d1': 0}}
        reopened._write_locked(data)
    status()
    assert payloads[-1][0]['state'] == 'done'
    assert payloads[-1][0]['reply'] == ''
    assert payloads[-1][0]['message_index'] == 0


def test_worker_skips_generation_when_delivery_arrives_during_wait(delivery, monkeypatch):
    store, _, _ = delivery
    generate = MagicMock()
    monkeypatch.setattr(routes, '_run_prime_brief_job_owned', generate)
    store.inject_assistant_message('Recap già salvato', {'brief_id': 'brief-d1'})
    routes._run_prime_brief_job('d1', 'brief-d1', 'msg', '.')
    generate.assert_not_called()
    assert routes._brief_job_get('d1')['state'] == 'done'
