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
        "staged": 0, "unstaged": 1, "untracked": 1,
        "dirty_files": [{"status": " M", "path": "api/routes.py"}, {"status": "??", "path": "new.py"}],
        "dirty_files_truncated": 0,
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


def test_manual_refresh_bypasses_cache_at_endpoint(monkeypatch):
    calls = []
    monkeypatch.setattr(repo_status, "get_repo_status", lambda **kwargs: calls.append(kwargs) or {"ok": True})
    assert routes.handle_get(_Handler(), urlparse("/api/repo-status?refresh=1")) is True
    assert calls == [{"force": True}]


def test_cache_force_expiry_and_ignored_branches(monkeypatch):
    calls = []
    clock = [100.0]
    monkeypatch.setattr(repo_status.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(repo_status, "_CACHE", {"expires_at": 0.0, "payload": None})
    monkeypatch.setitem(repo_status.REPO_STATUS_CONFIG, "repos", (("Hermes WebUI", Path("unused")),))
    def read(name, path, **kwargs):
        calls.append(kwargs)
        return _ok_repo(dirty=1 if len(calls) == 1 else 0)
    monkeypatch.setattr(repo_status, "read_repo_status", read)
    assert repo_status.get_repo_status()["repos"][0]["dirty"] == 1
    assert repo_status.get_repo_status()["repos"][0]["dirty"] == 1
    fresh = repo_status.get_repo_status(force=True)
    assert fresh["repos"][0]["dirty"] == 0
    assert fresh["checked_at"] > 0
    assert len(calls) == 2
    assert calls[0]["ignored_branches"] == ("master",)
    clock[0] += 11
    repo_status.get_repo_status()
    assert len(calls) == 3


def test_real_git_status_tracks_modify_stage_commit_and_ignored_files(tmp_path):
    import subprocess
    def git(*args):
        return subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True, text=True).stdout
    git("init")
    git("config", "user.name", "Repo Test")
    git("config", "user.email", "repo-test@example.test")
    (tmp_path / ".gitignore").write_text(".tmp/deleghe/\n")
    (tmp_path / "tracked.txt").write_text("initial")
    git("add", ".")
    git("commit", "-m", "initial")
    def status():
        return repo_status.read_repo_status("trial", tmp_path)
    initial = status()
    assert initial["dirty"] == 0
    assert initial["upstream"] is None
    (tmp_path / "tracked.txt").write_text("changed")
    (tmp_path / "new.txt").write_text("new")
    (tmp_path / ".tmp/deleghe").mkdir(parents=True)
    (tmp_path / ".tmp/deleghe/prompt.md").write_text("local prompt")
    dirty = status()
    assert (dirty["dirty"], dirty["unstaged"], dirty["untracked"]) == (2, 1, 1)
    assert {x["path"] for x in dirty["dirty_files"]} == {"tracked.txt", "new.txt"}
    git("add", ".")
    assert status()["staged"] == 2
    git("commit", "-m", "save changes")
    saved = status()
    assert saved["dirty"] == 0
    assert saved["dirty_files"] == []
    assert saved["head"]["hash"] != initial["head"]["hash"]


def test_dirty_file_details_are_bounded_but_total_is_exact():
    parsed = repo_status.parse_porcelain_status("## main\n" + "".join(f"?? f{i}.txt\n" for i in range(30)))
    assert parsed["dirty"] == parsed["untracked"] == 30
    assert len(parsed["dirty_files"]) == 20
    assert parsed["dirty_files_truncated"] == 10
