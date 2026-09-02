"""Regression: start.ps1's bind must survive server.py's repo .env load.

server.py loads the repository .env at import (commit 7d83e23b) with shell
``source`` semantics — .env overrides the ambient environment unless
HERMES_WEBUI_PRESERVE_ENV is set. start.ps1 resolves the bind host/port
itself and exports them, so without that flag a .env pinning
HERMES_WEBUI_HOST/PORT silently moves the WebUI to a different port.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]

STUB_SERVER = """\
import json
import os

from bootstrap import _load_repo_dotenv

_load_repo_dotenv()

print(json.dumps({
    "host": os.getenv("HERMES_WEBUI_HOST"),
    "port": os.getenv("HERMES_WEBUI_PORT"),
}))
"""


@pytest.mark.skipif(
    shutil.which("powershell") is None, reason="start.ps1 needs Windows PowerShell"
)
def test_start_ps1_bind_survives_repo_dotenv(tmp_path):
    """A launcher-resolved 0.0.0.0:8788 is not clobbered by .env's 127.0.0.1:8787."""
    shutil.copy(REPO_ROOT / "start.ps1", tmp_path / "start.ps1")
    shutil.copy(REPO_ROOT / "bootstrap.py", tmp_path / "bootstrap.py")
    (tmp_path / "server.py").write_text(STUB_SERVER, encoding="utf-8")
    (tmp_path / ".env").write_text(
        "HERMES_WEBUI_HOST=127.0.0.1\nHERMES_WEBUI_PORT=8787\n", encoding="utf-8"
    )
    agent_dir = tmp_path / "agent"
    (agent_dir / "hermes_cli").mkdir(parents=True)

    env = os.environ.copy()
    env["HERMES_WEBUI_HOST"] = "0.0.0.0"
    env["HERMES_WEBUI_PORT"] = "8788"
    env["HERMES_WEBUI_PYTHON"] = sys.executable
    env["HERMES_WEBUI_AGENT_DIR"] = str(agent_dir)
    env["HERMES_HOME"] = str(tmp_path / "home")
    env["HERMES_WEBUI_STATE_DIR"] = str(tmp_path / "state")

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(tmp_path / "start.ps1"),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"host": "0.0.0.0", "port": "8788"}, (
        "server.py's repo .env load overrode the bind start.ps1 resolved: "
        f"{payload}"
    )
