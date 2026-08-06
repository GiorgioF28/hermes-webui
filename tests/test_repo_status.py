import io
import json
from pathlib import Path
from urllib.parse import urlparse

from api import repo_status, routes


class _Handler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass


def test_parse_porcelain_status_with_tracking_counts_and_dirty_files():
    parsed = repo_status.parse_porcelain_status(
        "## feat/panel...origin/feat/panel [ahead 3, behind 1]\n M api/routes.py\n?? new.py\n"
    )
    assert parsed == {
        "branch": "feat/panel",
        "upstream": "origin/feat/panel",
        "ahead": 3,
        "behind": 1,
        "dirty": 2,
    }


def test_parse_porcelain_status_without_upstream():
    parsed = repo_status.parse_porcelain_status("## local-only\n")
    assert parsed["branch"] == "local-only"
    assert parsed["upstream"] is None
    assert parsed["ahead"] == parsed["behind"] == parsed["dirty"] == 0


def test_read_repo_status_degrades_on_git_error():
    def failing_runner(_repo: Path, _args: list[str], _timeout: float) -> str:
        raise OSError("git unavailable")

    result = repo_status.read_repo_status("Broken", Path("Z:/missing"), runner=failing_runner)
    assert result["name"] == "Broken"
    assert result["status"] == "errore"
    assert result["head"] is None


def test_repo_status_endpoint_returns_json(monkeypatch):
    payload = {
        "ok": True,
        "repos": [{"name": "Hermes WebUI", "status": "ok"}],
        "workflow": {"id": "vt67KEU7MY0vd9vJ", "label": "aggiornato via API"},
    }
    monkeypatch.setattr(repo_status, "get_repo_status", lambda: payload)
    handler = _Handler()

    assert routes.handle_get(handler, urlparse("/api/repo-status")) is True
    assert handler.status == 200
    assert json.loads(handler.wfile.getvalue()) == payload
