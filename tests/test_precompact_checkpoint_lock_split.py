"""Il checkpoint pre-compattazione non aspetta piu' i worker delle deleghe.

Prima il pass Librarian del checkpoint prendeva ``_DELEGATION_EXECUTION_LOCK``
(tenuto da ogni worker per tutta la delega, 10-15 minuti) con attesa massima
30s: con Prime che delega di continuo il checkpoint falliva quasi sempre, la
compattazione veniva rinviata (fail-closed) e il contesto saliva a 300-500k
token contro un cap di 80k.

Ora i due pass che scrivono memoria (checkpoint e sync post-delega) si
serializzano su ``_MEMORY_PASS_LOCK``; i worker non lo toccano (la memory
fence gia' impedisce loro di scrivere la memoria), quindi il checkpoint puo'
girare mentre una delega e' in corso.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from api import precompact_checkpoint as pc
from api import prime_delegation as pd


class _LoopThread:
    """Loop asyncio in un thread, con lo ``submit`` che il registry espone."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def submit(self, coro, timeout=None):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout)

    def close(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self._thread.join(timeout=5)


class _Registry:
    def __init__(self, loop_thread):
        self._loop = loop_thread


@pytest.fixture
def loop_thread(monkeypatch):
    lt = _LoopThread()
    # Lock freschi, legati al loop di test.
    monkeypatch.setattr(pd, "_DELEGATION_EXECUTION_LOCK", asyncio.Lock())
    monkeypatch.setattr(pd, "_MEMORY_PASS_LOCK", asyncio.Lock())
    monkeypatch.setenv(pc.ENV_LOCK_WAIT, "0.3")
    yield lt
    lt.close()


def _hold(loop_thread, lock):
    """Acquisisce ``lock`` sul loop di test e restituisce un evento per rilasciarlo."""
    release = asyncio.Event()

    async def _holder():
        async with lock:
            await release.wait()

    async def _acquire():
        release.clear()
        asyncio.ensure_future(_holder())
        for _ in range(50):
            if lock.locked():
                return
            await asyncio.sleep(0.01)
        raise AssertionError("lock non acquisito")

    loop_thread.submit(_acquire(), timeout=5)

    def _release():
        loop_thread.loop.call_soon_threadsafe(release.set)

    return _release


def test_checkpoint_runs_while_a_delegation_worker_holds_the_execution_lock(loop_thread, monkeypatch):
    calls = []

    async def fake_worker(prompt, model, workspace, **kw):
        calls.append((model, kw.get("agent_id")))
        return "memoria salvata"

    monkeypatch.setattr(pd, "_run_worker", fake_worker)
    monkeypatch.setattr(pd, "_load_memory_mcp_servers", lambda ws: {})
    release = _hold(loop_thread, pd._DELEGATION_EXECUTION_LOCK)
    try:
        out = pc.librarian_runner(_Registry(loop_thread), "ws")("salva questo")
    finally:
        release()
    assert out == "memoria salvata"
    assert calls and calls[0][0] == pd._LIBRARIAN_MODEL


def test_checkpoint_still_waits_for_a_running_memory_pass(loop_thread, monkeypatch):
    async def fake_worker(prompt, model, workspace, **kw):
        return "non dovrei girare"

    monkeypatch.setattr(pd, "_run_worker", fake_worker)
    monkeypatch.setattr(pd, "_load_memory_mcp_servers", lambda ws: {})
    release = _hold(loop_thread, pd._MEMORY_PASS_LOCK)
    try:
        with pytest.raises(pc.CheckpointError, match="lock memoria"):
            pc.librarian_runner(_Registry(loop_thread), "ws")("salva questo")
    finally:
        release()


def test_post_delegation_librarian_pass_holds_the_memory_lock(loop_thread, monkeypatch):
    seen = {}

    async def fake_serial(task_id, task_type, task, output, workspace, *, session_id="hermes-prime"):
        seen["memory_locked"] = pd._MEMORY_PASS_LOCK.locked()
        seen["exec_locked"] = pd._DELEGATION_EXECUTION_LOCK.locked()

    monkeypatch.setattr(pd, "_run_librarian_serial", fake_serial)
    loop_thread.submit(pd._run_librarian("d1", "codice", "task", "out", "ws"), timeout=5)
    assert seen == {"memory_locked": True, "exec_locked": True}
    assert not pd._MEMORY_PASS_LOCK.locked()


def test_default_lock_wait_covers_a_librarian_pass():
    """Un pass memoria dura fino a un paio di minuti: 30s era tarato sui worker."""
    assert pc.DEFAULT_LOCK_WAIT_SECONDS >= 120
