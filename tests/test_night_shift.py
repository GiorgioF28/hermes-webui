import os
from datetime import date, datetime

from api import night_shift


def _stamp(path, day: date):
    ts = datetime(day.year, day.month, day.day, 12).timestamp()
    os.utime(path, (ts, ts))


def test_build_briefing_packet_collects_expected_context(monkeypatch, tmp_path):
    target_day = date(2026, 6, 21)
    (tmp_path / "tasks").mkdir()
    today = tmp_path / "tasks" / "today.md"
    today.write_text("- [ ] fix Hermes bridge\n- [x] old done\n", encoding="utf-8")
    _stamp(today, target_day)

    projects_dir = tmp_path / "obsidian-vault" / "01-Projects"
    projects_dir.mkdir(parents=True)
    alpha = projects_dir / "Alpha.md"
    alpha.write_text(
        "# Alpha\n\nnext_action: ship packet\nblocks: waiting data\n\n## Next\n- [ ] draft plan\n",
        encoding="utf-8",
    )
    _stamp(alpha, target_day)

    docs = tmp_path / "docs"
    docs.mkdir()
    edited = docs / "runbook.md"
    edited.write_text("changed", encoding="utf-8")
    _stamp(edited, target_day)
    secret = docs / "api-token.txt"
    secret.write_text("secret", encoding="utf-8")
    _stamp(secret, target_day)

    projects = tmp_path / "projects"
    projects.mkdir()
    (projects / "project-inventory.csv").write_text("project,status\nAlpha,active\n", encoding="utf-8")
    (projects / "active-repos.csv").write_text("project,path\nAlpha,repo\n", encoding="utf-8")

    monkeypatch.setattr(
        night_shift.worklog,
        "build_worklog",
        lambda *args, **kwargs: {
            "recent": [
                {
                    "date": "2026-06-21",
                    "project": "Hermes",
                    "type": "task-done",
                    "summary": "fix bridge",
                    "source": "task:tasks/today.md",
                },
                {
                    "date": "2026-06-20",
                    "project": "Altro",
                    "type": "task-done",
                    "summary": "older",
                    "source": "task:x",
                },
            ]
        },
    )

    packet = night_shift.build_briefing_packet(tmp_path, target_day)

    assert packet["day"] == "2026-06-21"
    assert packet["worklog"]["count"] == 1
    assert packet["worklog"]["totals_by_project"] == {"Hermes": 1}
    assert {item["path"] for item in packet["edited_memory_files"]} == {
        "docs/runbook.md",
        "obsidian-vault/01-Projects/Alpha.md",
        "tasks/today.md",
    }
    assert packet["open_tasks_by_project"]["Hermes"][0]["task"] == "fix Hermes bridge"
    assert packet["open_tasks_by_project"]["Alpha"][0]["task"] == "draft plan"
    assert packet["projects"]["inventory"] == [{"project": "Alpha", "status": "active"}]
    assert packet["projects"]["active_repos"] == [{"project": "Alpha", "path": "repo"}]
    assert packet["projects"]["notes"][0]["next_action"] == ["ship packet"]
    assert packet["projects"]["notes"][0]["blocks"] == ["waiting data"]


def test_write_today_plan_archives_before_overwriting(tmp_path):
    today = tmp_path / "tasks" / "today.md"
    today.parent.mkdir()
    today.write_text("# Today - 2026-06-21\n\n- [ ] keep this archived\n", encoding="utf-8")

    result = night_shift.write_today_plan(
        tmp_path,
        {
            "date": "2026-06-22",
            "priorities": ["P0 Hermes Night Shift"],
            "routine": [{"time": "05:00-07:00", "title": "X, Y", "tasks": ["close yesterday"]}],
            "projects": [{"project": "Hermes", "next_action": "run tests", "tasks": ["ship module"]}],
        },
    )

    archive = tmp_path / result["archived_to"]
    assert archive.exists()
    assert archive.read_text(encoding="utf-8") == "# Today - 2026-06-21\n\n- [ ] keep this archived\n"
    body = today.read_text(encoding="utf-8")
    assert "# Today - 2026-06-22" in body
    assert "- [ ] P0 Hermes Night Shift" in body
    assert "- 05:00-07:00: X, Y" in body
    assert "### Hermes" in body
    assert "- [ ] ship module" in body


def test_archive_worklog_detail_appends_once(monkeypatch, tmp_path):
    monkeypatch.setattr(
        night_shift.worklog,
        "build_worklog",
        lambda *args, **kwargs: {
            "recent": [
                {
                    "date": "2026-06-21",
                    "project": "Hermes",
                    "type": "commit",
                    "summary": "add packet",
                    "source": "git:webui:abc",
                }
            ]
        },
    )

    first = night_shift.archive_worklog_detail(tmp_path, "2026-06-21")
    second = night_shift.archive_worklog_detail(tmp_path, "2026-06-21")

    path = tmp_path / first["path"]
    body = path.read_text(encoding="utf-8")
    assert first["changed"] is True
    assert second["changed"] is False
    assert body.count("## Night Shift worklog detail - 2026-06-21") == 1
    assert "[Hermes] commit: add packet" in body
