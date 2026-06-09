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
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd
    assert "--json" in cmd
    assert cmd[-2:] == ["-o", "/o.txt"]


def test_build_codex_cmd_resume_turn_full_agent():
    cmd = routes._build_codex_cmd(workspace="/ws", full_agent=True, resume_id="abc-123", output_path="/o.txt")
    assert "resume" in cmd and "abc-123" in cmd
    # resume subcommand comes right after exec
    assert cmd[1:4] == ["exec", "resume", "abc-123"]


def test_build_codex_cmd_non_full_agent_uses_workspace_write():
    cmd = routes._build_codex_cmd(workspace="/ws", full_agent=False, resume_id=None, output_path="/o.txt")
    assert "-s" in cmd and "workspace-write" in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
