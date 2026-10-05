import json
from types import SimpleNamespace

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
