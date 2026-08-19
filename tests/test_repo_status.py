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


# ── compute_live_behind (funzione pura) ──────────────────────────────────────


def _ok_repo(**kwargs):
    """Repo base 'ok' con tutti i campi necessari."""
    base = {
        "name": "TestRepo", "status": "ok",
        "branch": "main", "upstream": "origin/main",
        "ahead": 0, "behind": 0, "dirty": 0,
        "head": {"hash": "abc1234", "subject": "fix"},
        "stranded": [], "stranded_error": False,
    }
    base.update(kwargs)
    return base


def test_compute_live_behind_false_when_clean():
    result = repo_status.compute_live_behind(_ok_repo())
    assert result["live_behind"] is False
    assert result["live_behind_reasons"] == []


def test_compute_live_behind_dirty():
    result = repo_status.compute_live_behind(_ok_repo(dirty=3))
    assert result["live_behind"] is True
    assert "dirty" in result["live_behind_reasons"]


def test_compute_live_behind_ahead():
    result = repo_status.compute_live_behind(_ok_repo(ahead=2))
    assert result["live_behind"] is True
    assert "ahead" in result["live_behind_reasons"]


def test_compute_live_behind_behind():
    result = repo_status.compute_live_behind(_ok_repo(behind=1))
    assert result["live_behind"] is True
    assert "behind" in result["live_behind_reasons"]


def test_compute_live_behind_stranded():
    result = repo_status.compute_live_behind(
        _ok_repo(stranded=[{"branch": "feat/x", "commits": 2, "hash": "aaa", "subject": "wip"}])
    )
    assert result["live_behind"] is True
    assert "stranded" in result["live_behind_reasons"]


def test_compute_live_behind_multiple_reasons():
    result = repo_status.compute_live_behind(_ok_repo(dirty=1, ahead=3))
    assert result["live_behind"] is True
    assert set(result["live_behind_reasons"]) >= {"dirty", "ahead"}


def test_compute_live_behind_false_for_errore_repo():
    errore = {"name": "BrokenRepo", "status": "errore", "ahead": 5, "dirty": 2, "stranded": [{"branch": "x", "commits": 1}]}
    result = repo_status.compute_live_behind(errore)
    assert result["live_behind"] is False
    assert result["live_behind_reasons"] == []


def test_read_repo_status_includes_live_behind_fields(monkeypatch):
    """read_repo_status deve includere live_behind e live_behind_reasons nel payload."""
    calls = []

    def fake_runner(repo, args, timeout):
        calls.append(args)
        if args[0] == "status":
            return "## main...origin/main [ahead 1]\n M api/routes.py\n"
        if args[0] == "log":
            return "abc1234 fix something"
        if args[0] == "for-each-ref":
            return ""
        return ""

    result = repo_status.read_repo_status("TestRepo", Path("/fake"), runner=fake_runner)
    assert result["status"] == "ok"
    assert "live_behind" in result
    assert result["live_behind"] is True  # ahead=1
    assert "ahead" in result["live_behind_reasons"]


def test_read_repo_status_errore_includes_live_behind_fields():
    def failing_runner(_r, _a, _t):
        raise OSError("git unavailable")

    result = repo_status.read_repo_status("BrokenRepo", Path("Z:/missing"), runner=failing_runner)
    assert result["status"] == "errore"
    assert result["live_behind"] is False
    assert result["live_behind_reasons"] == []


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
