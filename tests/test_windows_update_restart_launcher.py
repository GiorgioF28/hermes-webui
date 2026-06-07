from pathlib import Path


def test_windows_update_restart_uses_start_ps1_with_current_port():
    source = (Path(__file__).resolve().parents[1] / "api" / "updates.py").read_text(
        encoding="utf-8"
    )

    assert 'start_script = Path(REPO_ROOT) / "start.ps1"' in source
    assert '"-File",' in source
    assert '"HERMES_WEBUI_PORT", "8787"' in source
    assert '"HERMES_WEBUI_HOST", "127.0.0.1"' in source
    assert "restart_cwd = str(REPO_ROOT)" in source
