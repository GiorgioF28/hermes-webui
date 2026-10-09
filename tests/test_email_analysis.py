import json
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from api.email_analysis import analyse_emails


def _emails():
    return [
        {
            "account": "gmail-personale",
            "from": "sender@example.test",
            "fromName": "Sender",
            "subject": "Decisione richiesta",
            "receivedAt": "2026-09-01T06:30:00Z",
            "bodyExcerpt": "Serve una risposta.",
        },
        {
            "account": "yahoo-personale",
            "from": "newsletter@example.test",
            "fromName": "Newsletter",
            "subject": "Novita",
            "receivedAt": "2026-09-01T06:40:00Z",
            "bodyExcerpt": "Aggiornamento.",
        },
    ]


class FakeMessages:
    def __init__(self, payload):
        self.payload = payload
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(self.payload))])


def test_analysis_parses_fake_client_and_bounds_fields():
    messages = FakeMessages([
        {"index": 0, "importance": "alta", "summary": "S" * 450, "why": "W" * 250},
        {"index": 1, "importance": "bassa", "summary": "Aggiornamento utile", "why": "informativo"},
    ])
    client = SimpleNamespace(messages=messages)

    rows, engine, error = analyse_emails(_emails(), client=client)

    assert engine == "prime" and error is None
    assert rows[0]["importance"] == "alta"
    assert len(rows[0]["summary"]) == 400
    assert len(rows[0]["why"]) == 200
    assert rows[1]["importance"] == "bassa"
    assert messages.kwargs["temperature"] == 0
    assert messages.kwargs["timeout"] == 120
    assert len(json.loads(messages.kwargs["messages"][0]["content"].split("\n", 1)[1])) == 2


def test_analysis_batches_all_rows_and_rejects_partial_batches():
    calls = []

    class BatchMessages:
        def create(self, **kwargs):
            rows = json.loads(kwargs["messages"][0]["content"].split("\n", 1)[1])
            calls.append(len(rows))
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps([
                {"index": row["index"], "importance": "media", "summary": "ok", "why": "utile"}
                for row in rows
            ]))])

    emails = _emails() * 41
    rows, engine, error = analyse_emails(emails, client=SimpleNamespace(messages=BatchMessages()))
    assert calls == [80, 2]
    assert engine == "prime" and error is None and len(rows) == 82

    class PartialMessages:
        def create(self, **_kwargs):
            return SimpleNamespace(content=[SimpleNamespace(text='[{"index":0,"importance":"alta"}]')])

    rows, engine, error = analyse_emails(_emails(), client=SimpleNamespace(messages=PartialMessages()))
    assert engine == "rules" and "ValueError" in error
    assert all(row["summary"] == "" and row["why"] == "" for row in rows)


def test_analysis_invalid_json_falls_back_deterministically():
    class BrokenMessages:
        def create(self, **_kwargs):
            return SimpleNamespace(content=[SimpleNamespace(text="not json")])

    rows, engine, error = analyse_emails(
        _emails(),
        client=SimpleNamespace(messages=BrokenMessages()),
    )

    assert engine == "rules"
    assert "JSONDecodeError" in error
    assert [row["importance"] for row in rows] == ["media", "bassa"]
    assert all(row["summary"] == "" and row["why"] == "" for row in rows)


def test_default_client_routes_configured_codex_through_prime_profile():
    result = [{"index": 0, "importance": "alta", "summary": "Decisione richiesta", "why": "Serve risposta"}]
    with patch("api.email_analysis._active_provider_id", return_value="openai-codex"), \
         patch("api.email_analysis._run_codex_analysis", return_value=json.dumps(result)) as run:
        rows, engine, error = analyse_emails(_emails()[:1])

    assert (engine, error) == ("prime", None)
    assert rows[0]["summary"] == "Decisione richiesta"
    prompt = run.call_args.args[0]
    assert '"subject": "Decisione richiesta"' in prompt
    assert "Restituisci solo il JSON" in prompt


def test_default_client_keeps_anthropic_route_when_selected(monkeypatch):
    import agent.anthropic_adapter as adapter
    import hermes_cli.auth as auth

    monkeypatch.setattr("api.email_analysis._active_provider_id", lambda: "anthropic")
    monkeypatch.setattr(adapter, "build_anthropic_client", lambda key, timeout: (key, timeout))
    monkeypatch.setattr(auth, "get_anthropic_key", lambda: "configured-through-auth-store")

    assert __import__("api.email_analysis", fromlist=["_default_client"])._default_client() == (
        "configured-through-auth-store", 120
    )


def test_codex_timeout_and_partial_json_preserve_rules_fallback():
    with patch("api.email_analysis._active_provider_id", return_value="codex-cli"), \
         patch("api.email_analysis._run_codex_analysis", side_effect=subprocess.TimeoutExpired("codex", 120)):
        rows, engine, error = analyse_emails(_emails()[:1])
    assert engine == "rules" and "TimeoutExpired" in error
    assert rows[0]["summary"] == rows[0]["why"] == ""

    with patch("api.email_analysis._active_provider_id", return_value="codex-cli"), \
         patch("api.email_analysis._run_codex_analysis", return_value='[{"index":0,"importance":"alta"}]'):
        rows, engine, error = analyse_emails(_emails())
    assert engine == "rules" and "ValueError" in error
    assert all(row["summary"] == "" and row["why"] == "" for row in rows)


def test_codex_cli_adapter_uses_bounded_read_only_prime_command(monkeypatch, tmp_path):
    from api import email_analysis

    monkeypatch.setattr("api.config.DEFAULT_WORKSPACE", tmp_path)
    monkeypatch.setattr("api.prime_delegation._resolve_codex_executable", lambda: "codex")
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(command=command, kwargs=kwargs)
        event = {"type": "item.completed", "item": {"type": "agent_message", "phase": "final", "text": "[]"}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(event), stderr="")

    monkeypatch.setattr(email_analysis.subprocess, "run", fake_run)
    assert email_analysis._run_codex_analysis("synthetic prompt") == "[]"
    assert "--sandbox" in captured["command"]
    assert captured["command"][captured["command"].index("--sandbox") + 1] == "read-only"
    assert captured["kwargs"]["timeout"] == 120
    assert captured["kwargs"]["shell"] is False
