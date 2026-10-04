from api import routes


def test_extract_codex_session_id_from_event():
    line = '{"type":"session.created","session_id":"abc-123"}'
    assert routes._codex_session_id_from_line(line) == "abc-123"


def test_extract_codex_session_id_nested():
    line = '{"type":"thread.started","thread":{"id":"t-9"}}'
    assert routes._codex_session_id_from_line(line) == "t-9"


def test_extract_codex_session_id_none_for_other():
    assert routes._codex_session_id_from_line('{"type":"item.completed"}') is None
    assert routes._codex_session_id_from_line('not json') is None


def test_build_codex_cmd_first_turn_full_agent():
    cmd = routes._build_codex_cmd(workspace="/ws", full_agent=True, resume_id=None, output_path="/o.txt")
    assert cmd[:2] == ["codex.cmd", "exec"]
    assert "resume" not in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "danger-full-access"
    assert cmd[cmd.index("-c") + 1] == 'approval_policy="never"'
    assert "--json" in cmd
    assert cmd[-2:] == ["-o", "/o.txt"]


def test_build_codex_cmd_resume_turn_full_agent():
    cmd = routes._build_codex_cmd(workspace="/ws", full_agent=True, resume_id="abc-123", output_path="/o.txt")
    assert "resume" in cmd and "abc-123" in cmd
    # Full Access options precede the resume subcommand
    assert cmd[cmd.index("resume") + 1] == "abc-123"
    assert cmd.index("--sandbox") < cmd.index("resume")


def test_build_codex_cmd_non_full_agent_uses_full_access():
    cmd = routes._build_codex_cmd(workspace="/ws", full_agent=False, resume_id=None, output_path="/o.txt")
    assert "-s" not in cmd
    assert cmd[cmd.index("--sandbox") + 1] == "danger-full-access"
    assert cmd[cmd.index("-c") + 1] == 'approval_policy="never"'
