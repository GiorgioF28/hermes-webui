import json
from datetime import date, datetime, timezone

import pytest

from api import daily_checklist


TODAY = date(2026, 8, 11)


def make_store(tmp_path):
    return daily_checklist.DailyChecklistStore(
        tmp_path,
        today_fn=lambda: TODAY,
        now_fn=lambda: datetime(2026, 8, 11, 12, tzinfo=timezone.utc),
    )


def expected_ids(store):
    definitions = json.loads(store.definitions_path.read_text(encoding="utf-8"))
    return store._expected_by_project(definitions)


def write_complete_days(store, rows):
    log = {}
    for raw_day, ids in rows.items():
        log[raw_day] = {"completed": sorted(ids), "items": {}, "updated_at": raw_day + "T12:00:00+00:00"}
    daily_checklist._atomic_write(store.log_path, log)


def test_seed_is_idempotent_and_never_overwrites_existing_file(tmp_path):
    store = make_store(tmp_path)
    first = store.get_state()
    assert len(first["projects"]) == 4
    assert first["total"] == 9

    definitions = json.loads(store.definitions_path.read_text(encoding="utf-8"))
    definitions["projects"][0]["name"] = "Nome personalizzato"
    daily_checklist._atomic_write(store.definitions_path, definitions)

    second = store.get_state()
    assert second["projects"][0]["name"] == "Nome personalizzato"


def test_toggle_is_idempotent_and_can_be_undone(tmp_path):
    store = make_store(tmp_path)
    store.get_state()

    once = store.toggle("vb-outreach", TODAY.isoformat(), True)
    twice = store.toggle("vb-outreach", TODAY.isoformat(), True)
    assert once["done"] == twice["done"] == 1
    log = json.loads(store.log_path.read_text(encoding="utf-8"))
    assert log[TODAY.isoformat()]["completed"] == ["vb-outreach"]

    undone = store.toggle("vb-outreach", TODAY.isoformat(), False)
    assert undone["done"] == 0
    log = json.loads(store.log_path.read_text(encoding="utf-8"))
    assert log[TODAY.isoformat()]["completed"] == []


def test_adhoc_exists_only_on_requested_date_and_delete_preserves_log(tmp_path):
    store = make_store(tmp_path)
    added = store.add_adhoc("ebook-amazon", "  Validare   copertina AI  ", "2026-08-10")
    adhoc = next(item for project in added["projects"] for item in project["items"] if item["adhoc"])
    assert adhoc["text"] == "Validare copertina AI"
    assert not any(item["adhoc"] for project in store.get_state("2026-08-11")["projects"] for item in project["items"])

    store.toggle(adhoc["id"], "2026-08-10", True)
    store.delete_adhoc(adhoc["id"])
    log = json.loads(store.log_path.read_text(encoding="utf-8"))
    assert adhoc["id"] in log["2026-08-10"]["completed"]
    assert store.get_state("2026-08-10")["total"] == 9


def test_streaks_handle_break_current_incomplete_day_and_record(tmp_path):
    store = make_store(tmp_path)
    store.get_state()
    expected = expected_ids(store)
    all_ids = set().union(*expected.values())
    write_complete_days(store, {
        "2026-08-07": all_ids,
        "2026-08-08": all_ids,
        "2026-08-09": set(),
        "2026-08-10": all_ids,
        "2026-08-11": {"vb-outreach"},
    })

    result = store.get_state()
    assert result["streaks"]["global"] == {"current": 1, "record": 2}
    assert result["streaks"]["projects"]["visionbuilts-clienti"] == {"current": 1, "record": 2}


def test_project_streak_is_independent_from_global_streak(tmp_path):
    store = make_store(tmp_path)
    store.get_state()
    expected = expected_ids(store)
    vision = expected["visionbuilts-clienti"]
    write_complete_days(store, {
        "2026-08-09": vision,
        "2026-08-10": vision,
    })

    streaks = store.streaks()
    assert streaks["projects"]["visionbuilts-clienti"]["current"] == 2
    assert streaks["global"]["current"] == 0


@pytest.mark.parametrize("bad_date", ["11-08-2026", "2026-02-30", "2026-8-1", 123])
def test_rejects_invalid_dates(tmp_path, bad_date):
    with pytest.raises(ValueError, match="date"):
        make_store(tmp_path).get_state(bad_date)


def test_validates_text_boolean_and_project(tmp_path):
    store = make_store(tmp_path)
    store.get_state()
    with pytest.raises(ValueError, match="200"):
        store.add_adhoc("ebook-amazon", "x" * 201)
    with pytest.raises(ValueError, match="project_id"):
        store.add_adhoc("missing", "Task valida")
    with pytest.raises(ValueError, match="done"):
        store.toggle("vb-outreach", TODAY.isoformat(), "true")


def test_atomic_write_uses_replace_and_leaves_no_temp_file(tmp_path, monkeypatch):
    store = make_store(tmp_path)
    calls = []
    real_replace = daily_checklist.os.replace

    def tracked_replace(source, target):
        calls.append((source, target))
        return real_replace(source, target)

    monkeypatch.setattr(daily_checklist.os, "replace", tracked_replace)
    store.get_state()

    assert calls
    assert calls[0][1] == store.definitions_path
    assert json.loads(store.definitions_path.read_text(encoding="utf-8"))["projects"]
    assert not list(tmp_path.glob("*.tmp"))


def test_recurring_daily_can_be_added_renamed_and_deactivated_via_config(tmp_path):
    store = make_store(tmp_path)
    added = store.configure({"action": "add_daily", "project_id": "ebook-amazon", "text": "Nuova daily"})
    item = next(item for project in added["projects"] if project["id"] == "ebook-amazon" for item in project["items"] if item["text"] == "Nuova daily")
    updated = store.configure({"action": "update_daily", "project_id": "ebook-amazon", "id": item["id"], "text": "Daily rinominata", "active": False})
    assert not any(row["text"] == "Daily rinominata" for project in updated["projects"] for row in project["items"])


def test_routes_and_frontend_expose_complete_daily_contract():
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    routes = (root / "api" / "routes.py").read_text(encoding="utf-8")
    frontend = (root / "static" / "command_bridge.js").read_text(encoding="utf-8")
    html = (root / "static" / "index.html").read_text(encoding="utf-8")
    for endpoint in (
        "/api/daily-checklist",
        "/api/daily-checklist/toggle",
        "/api/daily-checklist/adhoc",
        "/api/daily-checklist/streaks",
    ):
        assert endpoint in routes
        assert endpoint in frontend or endpoint.endswith("/streaks")
    assert 'id="dailyChecklistTemplate"' in html
    assert "modifica annullata" in frontend
