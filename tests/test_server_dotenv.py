"""Regression coverage for direct ``server.py`` repository .env loading."""

import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent


def test_server_import_loads_repo_dotenv_before_api_config(tmp_path):
    """A direct server startup exposes repository .env values to the process."""
    marker = "HERMES_TEST_SERVER_DOTENV_MARKER"
    marker_value = "loaded-from-test-dotenv"
    (tmp_path / "server.py").write_text(
        (REPO_ROOT / "server.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / "bootstrap.py").write_text(
        (REPO_ROOT / "bootstrap.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (tmp_path / ".env").write_text(f"{marker}={marker_value}\n", encoding="utf-8")

    env = os.environ.copy()
    env.pop(marker, None)
    env["HERMES_WEBUI_TEST_NETWORK_BLOCK"] = "1"
    command = (
        "import os, runpy, sys; "
        f"sys.path[:0] = [{str(tmp_path)!r}, {str(REPO_ROOT)!r}]; "
        f"runpy.run_path({str(tmp_path / 'server.py')!r}, run_name='server_import_test'); "
        f"assert os.environ.get({marker!r}) == {marker_value!r}"
    )

    result = subprocess.run(
        [sys.executable, "-c", command],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    source = (REPO_ROOT / "server.py").read_text(encoding="utf-8")
    assert source.index("_load_repo_dotenv()") < source.index("from api.config import")
