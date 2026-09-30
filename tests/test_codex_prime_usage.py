import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest
from api import codex_prime, codex_profiles, routes


def test_chief_label_matches_execution_profile_not_global_config():
    assert routes._prime_lead_model_id("codex") == "codex:gpt-6.1-sol"
    assert codex_profiles.profile(chief=True) == {"model": "gpt-6.1-sol", "reasoning_effort": "high"}
    assert codex_profiles.profile() == {"model": "gpt-6-luna", "reasoning_effort": "medium"}


def test_history_is_bounded_preserves_identity_and_does_not_mutate_originals():
    messages = [{"role":"assistant", "content":"x" * 20000, "usage":{"input_tokens":1000000},
                 "task_id":f"d{i}", "tool_events":["diagnostic" * 2000]} for i in range(50)]
    original = copy.deepcopy(messages)
    packet = codex_prime.prompt_history(messages)
    assert len(json.dumps(packet["messages"], ensure_ascii=False)) <= 48000
    assert packet["messages"][-1]["index"] == 49
    assert packet["messages"][-1]["task_id"] == "d49"
    assert packet["messages"][-1]["content_truncated"] is True
    assert packet["messages"][-1]["content_chars"] == 20000
    assert packet["older_messages_omitted"] > 0
    assert all("usage" not in x and "tool_events" not in x for x in packet["messages"])
    assert messages == original
    assert "prime_history" in packet["retrieval"]


def test_short_history_and_attachment_references_remain_available():
    messages = [{"role":"user", "content":"Continue the task", "attachments":[{"path":"image.png", "name":"image", "base64":"DO_NOT_EMBED"}]}]
    row = codex_prime.prompt_history(messages)["messages"][0]
    assert row["content"] == messages[0]["content"]
    assert row["attachments"] == [{"path":"image.png", "name":"image"}]
    assert codex_prime.prompt_history([])["messages"] == []


def test_cache_badges_support_raw_codex_history_without_double_counting():
    node=shutil.which("node")
    if not node: pytest.skip("Node unavailable")
    source=(Path(__file__).resolve().parents[1]/"static/command_bridge.js").read_text(encoding="utf8")
    functions=source[source.index("  function _fmtCompactTokens"):source.index("  function _formatQuotaMoneyShort")]
    checks = r"""
const assert=require('node:assert/strict');
const usage={input_tokens:1058879,cached_input_tokens:981504,output_tokens:2649,cache_write_input_tokens:0};
const badge=_formatAssistantUsageBadge(usage);
assert(badge.includes('in 1.1M'));
assert(badge.includes('cache letta 982k (93%)'));
assert(badge.includes('non cache 77k'));
assert.equal(_usageInputTokens(usage),1058879);
assert(_bridgeUsageTitle(usage).includes('somma delle chiamate'));
assert(_formatLiveUsage(usage).includes('cache letta'));
assert(_formatAssistantUsageBadge({input_tokens:100,cached_input_tokens:0,output_tokens:1}).includes('cache letta 0 (0%)'));
assert(!_formatAssistantUsageBadge({input_tokens:100,output_tokens:1}).includes('cache'));
assert(_formatAssistantUsageBadge({input_tokens:100,cache_read_input_tokens:2000,cache_creation_input_tokens:50}).includes('cache 2k/50'));
assert.equal(_usageCacheWriteTokens({cache_write_input_tokens:123}),123);
"""
    result=subprocess.run([node,"-"],input=functions+checks,capture_output=True,text=True,encoding="utf8",timeout=20)
    assert result.returncode == 0, result.stdout+result.stderr


def test_codex_memory_budget_overrides_large_runtime_setting(monkeypatch, tmp_path):
    from api import memory_retrieval as mr, prime_lean_preset, prime_session_store
    monkeypatch.setenv("HERMES_PRIME_MEMORY_MAX_CHARS", "120000")
    monkeypatch.setattr(prime_lean_preset, "prime_context_profile", lambda: "unlocked")
    monkeypatch.setattr(routes, "_prime_system_prompt_for_user", lambda *args: "PERSONA")
    monkeypatch.setattr(prime_session_store, "get_prime_session_store", lambda sid: type("Store", (), {"history": lambda self: {"messages": []}})())
    observed = []
    def memory(*args, **kwargs):
        observed.append(kwargs["max_chars"])
        return "MEMORY"
    monkeypatch.setattr(mr, "build_prime_unlocked_memory_detail", memory)
    prompt = codex_prime.build_prompt("REQUEST", tmp_path, session_id="test", user="giorgio")
    assert observed == [12000]
    assert "PERSONA" in prompt and "MEMORY" in prompt and prompt.endswith("REQUEST")


def test_memory_entrypoint_honors_budget_and_retains_legacy_default(monkeypatch, tmp_path):
    from api import memory_retrieval as mr
    monkeypatch.setattr(mr, "find_prime_memory_dir", lambda: tmp_path)
    observed = []
    monkeypatch.setattr(mr, "build_unlocked_memory_detail", lambda *args, **kwargs: observed.append(kwargs["max_chars"]) or "notes")
    mr.build_prime_unlocked_memory_detail("task", max_chars=12000)
    mr.build_prime_unlocked_memory_detail("task")
    assert observed == [12000, None]
