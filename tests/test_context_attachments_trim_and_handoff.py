import copy


def test_sanitize_replaces_old_historical_image_parts_without_mutating_source():
    from api.streaming import _sanitize_messages_for_api

    original = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "look at this"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
            "attachments": [{"path": "/tmp/attachments/shot.png"}],
        },
        {"role": "assistant", "content": "seen"},
        {"role": "user", "content": "turn two"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "turn three"},
        {"role": "assistant", "content": "ok"},
    ]
    before = copy.deepcopy(original)

    sanitized = _sanitize_messages_for_api(original)

    assert original == before
    first_content = sanitized[0]["content"]
    assert first_content[0] == {"type": "text", "text": "look at this"}
    assert first_content[1]["type"] == "text"
    assert "rimosso dal contesto" in first_content[1]["text"]
    assert "/tmp/attachments/shot.png" in first_content[1]["text"]
    assert "image_url" not in str(first_content)
    assert "attachments" not in sanitized[0]


def test_sanitize_keeps_recent_historical_image_parts_within_two_user_turns():
    from api.streaming import _sanitize_messages_for_api

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "recent image"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ],
        },
        {"role": "assistant", "content": "seen"},
        {"role": "user", "content": "only one later user turn"},
    ]

    sanitized = _sanitize_messages_for_api(messages)

    assert sanitized[0]["content"][1]["type"] == "image_url"


def test_handoff_summary_carries_live_facts_and_recent_context():
    from api.models import Session
    from api.routes import _build_session_handoff_summary

    session = Session(
        session_id="handoffsrc",
        title="Important task",
        messages=[
            {"role": "user", "content": "Decisione: usare il Gateway path."},
            {"role": "assistant", "content": "Stato task: backend pronto, manca UI."},
            {"role": "user", "content": "Prossima azione: aggiungi il bottone."},
        ],
    )

    summary = _build_session_handoff_summary(session)

    assert "Handoff dalla chat precedente" in summary
    assert "Important task (handoffsrc)" in summary
    assert "Decisione: usare il Gateway path" in summary
    assert "Stato task: backend pronto" in summary
    assert "Prossima azione: aggiungi il bottone" in summary
    assert "Riparti da questo handoff" in summary
