# api/persistent_agent_loop.py
"""Single asyncio loop thread owning persistent Claude Agent SDK sessions.

The WebUI runs on http.server.ThreadingHTTPServer (sync threads). The Claude
Agent SDK is asyncio and its clients are bound to the loop that created them, so
ALL clients live on ONE background loop. Sync worker threads submit coroutines
via asyncio.run_coroutine_threadsafe and block on the result.
"""
from __future__ import annotations

import asyncio
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

# factory(session_id, **kwargs) -> awaitable[client]
ClientFactory = Callable[..., Awaitable[Any]]


@dataclass
class _Entry:
    client: Any
    last_used: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class _LoopThread:
    """Owns one asyncio event loop running in a daemon thread."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, name="hermes-agent-loop", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def submit(self, coro: Awaitable[Any], timeout: Optional[float] = None) -> Any:
        fut = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return fut.result(timeout=timeout)

    def submit_future(self, coro: Awaitable[Any]):
        """Schedule a coroutine and return its concurrent.futures.Future.

        Unlike submit(), this does not block: the caller can poll the future
        with its own (e.g. progress-aware) deadline. Cancelling the returned
        future propagates cancellation to the underlying asyncio task.
        """
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def submit_nowait(self, coro: Awaitable[Any]) -> None:
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def stop(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)


class ClaudeSessionRegistry:
    def __init__(
        self,
        factory: ClientFactory,
        idle_ttl: float = 1800.0,
        max_sessions: int = 12,
        pinned_ids: Optional[set[str]] = None,
    ) -> None:
        self._factory = factory
        self._idle_ttl = idle_ttl
        self._max_sessions = max_sessions
        # Sessioni "pinnate": il capo persistente (hermes-prime) NON va mai chiuso
        # per inattivita' ne' sfrattato. Cosi' una pausa dell'utente non distrugge
        # il contesto e non costringe a re-iniettare il system prompt pesante.
        self._pinned: set[str] = set(pinned_ids or ())
        self._loop = _LoopThread()
        self._entries: dict[str, _Entry] = {}
        self._mutex = threading.Lock()

    def get(self, session_id: str) -> Optional[Any]:
        with self._mutex:
            e = self._entries.get(session_id)
            return e.client if e else None

    def get_or_create(self, session_id: str, **factory_kwargs: Any) -> Any:
        with self._mutex:
            e = self._entries.get(session_id)
            if e is not None:
                e.last_used = time.time()
                return e.client
            if len(self._entries) >= self._max_sessions:
                self._evict_oldest_locked()
            client = self._loop.submit(self._factory(session_id, **factory_kwargs), timeout=120)
            self._entries[session_id] = _Entry(client=client)
            return client

    def run_turn(self, session_id: str, coro_factory: Callable[[Any], Awaitable[Any]], timeout: Optional[float] = None) -> Any:
        """Run an async turn against the session's client, serialized per session."""
        client = self.get(session_id)
        if client is None:
            raise KeyError(session_id)
        async def _guarded():
            entry = self._entries[session_id]
            async with entry.lock:
                return await coro_factory(client)
        with self._mutex:
            self._entries[session_id].last_used = time.time()
        return self._loop.submit(_guarded(), timeout=timeout)

    def submit_turn(self, session_id: str, coro_factory: Callable[[Any], Awaitable[Any]]):
        """Like run_turn but returns the future without blocking.

        Lets the caller enforce a progress-aware deadline (e.g. abort only when
        the turn truly stalls) instead of a fixed wall-clock timeout.
        """
        client = self.get(session_id)
        if client is None:
            raise KeyError(session_id)
        async def _guarded():
            entry = self._entries[session_id]
            async with entry.lock:
                return await coro_factory(client)
        with self._mutex:
            self._entries[session_id].last_used = time.time()
        return self._loop.submit_future(_guarded())

    def close(self, session_id: str) -> None:
        with self._mutex:
            e = self._entries.pop(session_id, None)
        if e is not None:
            self._safe_disconnect(e.client)

    def sweep_idle(self, now: Optional[float] = None) -> None:
        now = now or time.time()
        to_close = []
        with self._mutex:
            for sid, e in list(self._entries.items()):
                if sid in self._pinned:
                    continue
                if now - e.last_used > self._idle_ttl:
                    to_close.append(self._entries.pop(sid).client)
        for c in to_close:
            self._safe_disconnect(c)

    def _evict_oldest_locked(self) -> None:
        # Non sfrattare mai il capo pinnato: scegli il piu' vecchio tra gli effimeri.
        candidates = [(sid, e) for sid, e in self._entries.items() if sid not in self._pinned]
        if not candidates:
            return
        oldest = min(candidates, key=lambda kv: kv[1].last_used)[0]
        client = self._entries.pop(oldest).client
        self._safe_disconnect(client)

    def _safe_disconnect(self, client: Any) -> None:
        async def _dc():
            try:
                await client.disconnect()
            except Exception:
                logger.debug("client disconnect failed", exc_info=True)
        try:
            self._loop.submit_nowait(_dc())
        except Exception:
            logger.debug("disconnect submit failed", exc_info=True)

    def shutdown(self) -> None:
        with self._mutex:
            clients = [e.client for e in self._entries.values()]
            self._entries.clear()
        for c in clients:
            self._safe_disconnect(c)
        self._loop.stop()
