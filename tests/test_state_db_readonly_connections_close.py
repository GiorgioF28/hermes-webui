"""Le connessioni state.db in sola lettura vengono chiuse (upstream #6823).

`with sqlite3.connect(...) as conn:` NON chiude la connessione: il context
manager di sqlite3 gestisce solo commit/rollback. Le letture di scoperta e
recupero sessioni aprivano una connessione ro per chiamata e la lasciavano al
garbage collector: handle e lock sul file (Windows) che si accumulano su un
server acceso per giorni. `closing(...)` la chiude sempre.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from api import session_discoverability, session_recovery

API = Path(__file__).resolve().parents[1] / "api"


def test_no_readonly_state_db_connection_is_left_to_the_gc():
    for name in ("session_discoverability.py", "session_recovery.py"):
        src = (API / name).read_text(encoding="utf-8")
        leaks = [
            line.strip()
            for line in src.splitlines()
            if re.search(r"with\s+sqlite3\.connect\(", line) and "closing(" not in line
        ]
        assert leaks == [], f"{name}: connessioni aperte senza closing(): {leaks}"


class _FakeCursor:
    def fetchall(self):
        return []

    def fetchone(self):
        return None

    def __iter__(self):
        return iter(())


class _SpyConnection:
    def __init__(self):
        self.closed = False
        self.row_factory = None

    def execute(self, *a, **k):
        return _FakeCursor()

    def cursor(self):
        return _FakeCursor()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def close(self):
        self.closed = True


@pytest.fixture
def spy(monkeypatch, tmp_path):
    db = tmp_path / "state.db"
    db.write_bytes(b"")
    created = []

    def fake_connect(*a, **k):
        conn = _SpyConnection()
        created.append(conn)
        return conn

    monkeypatch.setattr(session_discoverability.sqlite3, "connect", fake_connect)
    monkeypatch.setattr(session_recovery.sqlite3, "connect", fake_connect)
    return db, created


def test_discoverability_reader_closes_its_connection(spy):
    db, created = spy
    session_discoverability._read_state_db(db)
    assert created and all(c.closed for c in created)


def test_recovery_has_session_closes_its_connection(spy):
    db, created = spy
    session_recovery._state_db_has_session("sid", db)
    assert created and all(c.closed for c in created)
