import api.lead_brain as lb


def test_default_lead_is_claude(tmp_path):
    assert lb.get_lead(tmp_path) == lb.LEAD_CLAUDE
    state = lb.get_lead_state(tmp_path)
    assert state["lead"] == lb.LEAD_CLAUDE


def test_set_and_read_lead_roundtrip(tmp_path):
    state = lb.set_lead(tmp_path, lb.LEAD_CODEX, reason="anthropic-exhausted")
    assert state["lead"] == lb.LEAD_CODEX
    assert state["reason"] == "anthropic-exhausted"
    assert state["since"]  # timestamp present
    # persisted + re-read
    assert lb.get_lead(tmp_path) == lb.LEAD_CODEX
    assert (tmp_path / "tasks" / "lead-brain.json").is_file()
    assert state["manual"] is False


def test_manual_pin_persists_and_auto_mode_clears_it(tmp_path):
    state = lb.set_lead(tmp_path, lb.LEAD_CLAUDE, reason="manuale", manual=True)
    assert state["manual"] is True
    assert lb.get_lead_state(tmp_path)["manual"] is True
    state = lb.set_auto_failover(tmp_path)
    assert state["lead"] == lb.LEAD_CLAUDE
    assert state["manual"] is False



def test_set_lead_rejects_garbage_falls_back_to_claude(tmp_path):
    state = lb.set_lead(tmp_path, "gemma", reason="x")
    assert state["lead"] == lb.LEAD_CLAUDE


def test_quota_error_detection():
    assert lb.is_claude_quota_error(RuntimeError("HTTP 429 rate_limit_error"))
    assert lb.is_claude_quota_error(Exception("Your credit balance is too low"))
    assert lb.is_claude_quota_error(Exception("usage limit reached"))
    # transient / unrelated → NON deve flippare il capo
    assert not lb.is_claude_quota_error(Exception("overloaded_error: try again"))
    assert not lb.is_claude_quota_error(ConnectionError("connection reset"))
    assert not lb.is_claude_quota_error(None)


class _FakeResult:
    """Minimo stub di ResultMessage del Claude SDK per i test."""

    def __init__(self, is_error=False, api_error_status=None, subtype="success",
                 result=None, stop_reason=None, errors=None):
        self.is_error = is_error
        self.api_error_status = api_error_status
        self.subtype = subtype
        self.result = result
        self.stop_reason = stop_reason
        self.errors = errors


def test_result_message_quota_429_triggers_handoff():
    # Caso reale: crediti finiti → is_error con subtype 'success' + status 429.
    msg = _FakeResult(is_error=True, api_error_status=429, subtype="success")
    assert lb.result_message_quota_reason(msg)


def test_result_message_429_keeps_the_real_detail():
    # Senza il testo del result l'utente vede solo "429" e non sa che il modello
    # non e' incluso nel piano (caso Fable 5 su Pro, 2026-08-01).
    msg = _FakeResult(
        is_error=True, api_error_status=429, subtype="success",
        result="Fable 5 requires usage credits. Run /usage-credits to continue.",
    )
    reason = lb.result_message_quota_reason(msg)
    assert "429" in reason
    assert "requires usage credits" in reason


def test_result_message_quota_text_marker():
    msg = _FakeResult(is_error=True, result="Your credit balance is too low")
    assert lb.result_message_quota_reason(msg)


def test_result_message_5xx_is_transient_no_handoff():
    # 529 overloaded / 500 = intoppo transitorio, NON deve flippare il capo.
    assert not lb.result_message_quota_reason(_FakeResult(is_error=True, api_error_status=529))
    assert not lb.result_message_quota_reason(_FakeResult(is_error=True, api_error_status=500))


def test_result_message_success_no_handoff():
    assert not lb.result_message_quota_reason(_FakeResult(is_error=False, subtype="success"))
    assert not lb.result_message_quota_reason(None)
    # errore generico non di quota (es. max_turns) → nessun handoff
    assert not lb.result_message_quota_reason(
        _FakeResult(is_error=True, subtype="error_max_turns", result="stopped")
    )


def test_parse_brain_command():
    assert lb.parse_brain_command("/brain codex") == lb.LEAD_CODEX
    assert lb.parse_brain_command("/brain claude") == lb.LEAD_CLAUDE
    assert lb.parse_brain_command("/brain") == "status"
    assert lb.parse_brain_command("/brain auto") == "auto"
    assert lb.parse_brain_command("/capo") == "status"
    # forme naturali brevi
    assert lb.parse_brain_command("passa a codex") == lb.LEAD_CODEX
    assert lb.parse_brain_command("torna a claude") == lb.LEAD_CLAUDE
    # NON deve scattare su frasi di conversazione che citano i nomi
    assert lb.parse_brain_command("delega questo lavoro pesante a codex per favore") is None
    assert lb.parse_brain_command("claude mi sembra piu' lento oggi che ne pensi") is None
    assert lb.parse_brain_command("") is None
    assert lb.parse_brain_command("ciao come va") is None


def test_handoff_packet_includes_resume_and_message(tmp_path):
    tasks = tmp_path / "tasks"
    tasks.mkdir()
    (tasks / "active-context.md").write_text(
        "# Active Context\n\n## ▶ RIPRENDI DA QUI\n\nFare la cosa X poi la Y.\n\n---\n\n## Goal\nblah",
        encoding="utf-8",
    )
    packet = lb.build_handoff_packet(tmp_path, user_message="dammi il brief", partial_reply="Stavo dicendo")
    assert "Fare la cosa X poi la Y." in packet
    assert "Goal" not in packet  # si ferma al separatore ---
    assert "dammi il brief" in packet
    assert "Stavo dicendo" in packet


def test_handoff_packet_survives_missing_active_context(tmp_path):
    packet = lb.build_handoff_packet(tmp_path, user_message="ciao")
    assert "ciao" in packet
