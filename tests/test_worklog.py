from datetime import datetime, timezone
from pathlib import Path

from api import worklog


def test_classify_commit_type_from_conventional_prefix():
    assert worklog.classify_commit_type("feat: add work log") == "aggiunta"
    assert worklog.classify_commit_type("fix(api): handle empty days") == "correzione"
    assert worklog.classify_commit_type("perf: speed up cache") == "miglioria"
    assert worklog.classify_commit_type("docs: update readme") == "commit"


def test_project_for_keyword_mapping():
    assert worklog.project_for("prime watchdog webui") == "Hermes"
    assert worklog.project_for("n8n instagram recipe console") == "VisionBuilts"
    assert worklog.project_for("Hermes VisionBuilts integration") == "Hermes"
    assert worklog.project_for("vending shaker proteici Up Level Sicilia") == "Vending Machine"
    assert worklog.project_for("trading smart money trap") == "Trading"
    assert worklog.project_for("ebook cucina Amazon KDP") == "Ebook Cucina Amazon"
    assert worklog.project_for("Suno album rap") == "Altro"
    assert worklog.project_for("pay bills") == "Altro"


def test_parse_done_tasks_uses_explicit_date_and_file_mtime(tmp_path):
    path = tmp_path / "tasks" / "today.md"
    path.parent.mkdir()
    path.write_text(
        "- [x] 2026-06-20 fix prime bridge\n"
        "- [ ] open task\n"
        "- [X] publish VisionBuilts recipe pdf\n",
        encoding="utf-8",
    )
    ts = datetime(2026, 6, 21, 12, tzinfo=timezone.utc).timestamp()
    path.touch()
    import os

    os.utime(path, (ts, ts))

    events = worklog.parse_done_tasks(path, tmp_path)

    assert len(events) == 2
    assert events[0].date == "2026-06-20"
    assert events[0].project == "Hermes"
    assert events[0].type == "task-done"
    assert events[1].date == "2026-06-21"
    assert events[1].project == "VisionBuilts"


def test_build_worklog_buckets_tasks_and_dedupes(monkeypatch, tmp_path):
    fixed = datetime(2026, 6, 22, 9, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed if tz else fixed.replace(tzinfo=None)

    monkeypatch.setattr(worklog, "datetime", FixedDateTime)
    monkeypatch.setattr(worklog, "_git_events", lambda *args, **kwargs: [])

    today = tmp_path / "tasks" / "today.md"
    today.parent.mkdir()
    today.write_text(
        "- [x] fix Hermes prime\n"
        "- [x] fix Hermes prime\n"
        "- [x] Suno rap album\n"
        "- [x] validare vending shaker proteici\n"
        "- [x] preparare Amazon KDP cookbook\n",
        encoding="utf-8",
    )
    ts = fixed.timestamp()
    import os

    os.utime(today, (ts, ts))

    data = worklog.build_worklog(tmp_path, tmp_path / "webui", days=3)

    assert data["today_count"] == 4
    assert data["yesterday_count"] == 0
    assert data["record_count"] == 4
    assert data["streak_days"] == 1
    assert data["totals_by_project"]["Hermes"] == 1
    assert data["totals_by_project"]["Vending Machine"] == 1
    assert data["totals_by_project"]["Ebook Cucina Amazon"] == 1
    assert data["totals_by_project"]["Altro"] == 1
    assert "Rap" not in data["totals_by_project"]
    assert [row["count"] for row in data["daily"]] == [0, 0, 4]
    assert len(data["recent"]) == 4


def test_tracked_project_payload_matches_widget_contract():
    data = worklog.build_worklog(Path("missing-workspace"), Path("missing-webui"), days=1)

    assert [(row["name"], row["label"]) for row in data["tracked_projects"]] == [
        ("Hermes", "Hermes"),
        ("VisionBuilts", "VisionBuilts"),
        ("Vending Machine", "Vending"),
        ("Trading", "Trading"),
        ("Ebook Cucina Amazon", "Ebook Amazon"),
    ]
    js = (Path(__file__).parents[1] / "static" / "command_bridge.js").read_text(encoding="utf-8")
    assert "data.tracked_projects" in js
    assert "var projects = ['Hermes', 'VisionBuilts', 'Rap'];" not in js


def test_clamp_days_bounds():
    assert worklog.clamp_days("bad") == 14
    assert worklog.clamp_days("0") == 1
    assert worklog.clamp_days("999") == 90
