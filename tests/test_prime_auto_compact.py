import asyncio
import json

from api import prime_auto_compact


class _ResultMessage:
    def __init__(self, usage):
        self.usage = usage


class _FakeClient:
    def __init__(self, after_usage):
        self.queries = []
        self._after_usage = after_usage

    async def query(self, message):
        self.queries.append(message)

    async def receive_response(self):
        yield _ResultMessage(self._after_usage)


class _FakeRegistry:
    def __init__(self, after_usage=None):
        self.client = _FakeClient(after_usage or {})
        self.turns = []

    def run_turn(self, session_id, drive, timeout=None):
        self.turns.append((session_id, timeout))
        return asyncio.run(drive(self.client))


def _setup(monkeypatch, tmp_path, *, threshold=100, cooldown=0):
    monkeypatch.setenv("PRIME_COMPACT_THRESHOLD", str(threshold))
    monkeypatch.setenv("PRIME_COMPACT_COOLDOWN_TURNS", str(cooldown))
    monkeypatch.setenv("PRIME_AUTO_COMPACT_EVENTS", str(tmp_path / "prime-auto.jsonl"))
    monkeypatch.setenv("PRIME_AUTO_COMPACT_ENABLED", "1")
    prime_auto_compact.reset_state_for_tests(
        threshold_tokens=threshold,
        cooldown_turns=cooldown,
    )


def test_threshold_exceeded_and_idle_sends_hidden_compact_turn(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, threshold=100, cooldown=0)
    reg = _FakeRegistry(
        after_usage={
            "input_tokens": 12,
            "cache_read_input_tokens": 3,
            "cache_creation_input_tokens": 1,
        }
    )

    result = prime_auto_compact.maybe_auto_compact_prime(
        reg,
        session_id="hermes-prime",
        usage={"input_tokens": 80, "cache_read_input_tokens": 30},
        idle=True,
    )

    assert result["compacted"] is True
    assert reg.client.queries == ["/compact"]
    assert result["before_tokens"] == 110
    assert result["after_tokens"] == 16
    rows = [
        json.loads(line)
        for line in prime_auto_compact.event_log_path().read_text(encoding="utf-8").splitlines()
    ]
    assert rows[-1]["event"] == "prime_auto_compact"
    assert rows[-1]["status"] == "ok"
    assert rows[-1]["before_tokens"] == 110


def test_threshold_not_exceeded_is_noop(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, threshold=100, cooldown=0)
    reg = _FakeRegistry()

    result = prime_auto_compact.maybe_auto_compact_prime(
        reg,
        session_id="hermes-prime",
        usage={"input_tokens": 40, "cache_read_input_tokens": 59},
        idle=True,
    )

    assert result == {"compacted": False, "reason": "below_threshold", "before_tokens": 99}
    assert reg.client.queries == []
    assert not prime_auto_compact.event_log_path().exists()


def test_busy_session_is_noop(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, threshold=100, cooldown=0)
    reg = _FakeRegistry()

    result = prime_auto_compact.maybe_auto_compact_prime(
        reg,
        session_id="hermes-prime",
        usage={"input_tokens": 80, "cache_read_input_tokens": 30},
        idle=False,
    )

    assert result == {"compacted": False, "reason": "busy", "before_tokens": 110}
    assert reg.client.queries == []
    assert not prime_auto_compact.event_log_path().exists()


def test_cooldown_is_respected(monkeypatch, tmp_path):
    _setup(monkeypatch, tmp_path, threshold=100, cooldown=2)
    reg = _FakeRegistry()

    first = prime_auto_compact.maybe_auto_compact_prime(
        reg,
        session_id="hermes-prime",
        usage={"input_tokens": 101},
        idle=True,
    )
    second = prime_auto_compact.maybe_auto_compact_prime(
        reg,
        session_id="hermes-prime",
        usage={"input_tokens": 99},
        idle=True,
    )
    third = prime_auto_compact.maybe_auto_compact_prime(
        reg,
        session_id="hermes-prime",
        usage={"input_tokens": 101},
        idle=True,
    )

    assert first["compacted"] is True
    assert second["reason"] == "below_threshold"
    assert third["reason"] == "cooldown"
    assert reg.client.queries == ["/compact"]
