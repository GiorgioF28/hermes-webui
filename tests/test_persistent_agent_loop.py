# tests/test_persistent_agent_loop.py
import time
import pytest
from api import persistent_agent_loop as pal


class FakeClient:
    def __init__(self): self.connected = False; self.disconnected = False
    async def connect(self): self.connected = True
    async def disconnect(self): self.disconnected = True


def test_get_or_create_reuses_same_client(monkeypatch):
    created = []
    async def _factory(session_id, **kw):
        c = FakeClient(); await c.connect(); created.append(c); return c
    reg = pal.ClaudeSessionRegistry(factory=_factory, idle_ttl=100)
    c1 = reg.get_or_create("s1", cwd="/tmp", system_prompt="x")
    c2 = reg.get_or_create("s1", cwd="/tmp", system_prompt="x")
    assert c1 is c2
    assert len(created) == 1
    reg.shutdown()


def test_close_disconnects_client():
    async def _factory(session_id, **kw):
        c = FakeClient(); await c.connect(); return c
    reg = pal.ClaudeSessionRegistry(factory=_factory, idle_ttl=100)
    c1 = reg.get_or_create("s1", cwd="/tmp", system_prompt="x")
    reg.close("s1")
    for _ in range(50):
        if c1.disconnected: break
        time.sleep(0.02)
    assert c1.disconnected is True
    reg.shutdown()


def test_idle_eviction_disconnects():
    async def _factory(session_id, **kw):
        c = FakeClient(); await c.connect(); return c
    reg = pal.ClaudeSessionRegistry(factory=_factory, idle_ttl=0.1)
    c1 = reg.get_or_create("s1", cwd="/tmp", system_prompt="x")
    reg.sweep_idle(now=time.time() + 1.0)
    for _ in range(50):
        if c1.disconnected: break
        time.sleep(0.02)
    assert c1.disconnected is True
    assert reg.get("s1") is None
    reg.shutdown()
