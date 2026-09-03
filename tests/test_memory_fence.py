"""Recinto di scrittura sulla memoria per i sotto-agenti.

"Solo il Librarian scrive la memoria" era una regola di prompt: i worker
girano con bypassPermissions / --dangerously-bypass-approvals-and-sandbox e
possono scrivere il Vault. Il recinto fotografa le aree protette prima della
delega, ripristina cio' che e' stato toccato e lo segnala nell'esito.
"""

from __future__ import annotations

from pathlib import Path

from api import memory_fence


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "obsidian-vault" / "01-Projects").mkdir(parents=True)
    (tmp_path / "obsidian-vault" / "01-Projects" / "Progetto.md").write_text("stato: A\n", encoding="utf-8")
    (tmp_path / "MEMORY.md").write_text("# memoria\n", encoding="utf-8")
    (tmp_path / "graphify-out").mkdir()
    (tmp_path / "graphify-out" / "graph.json").write_text("{}", encoding="utf-8")
    (tmp_path / "hermes-webui").mkdir()
    (tmp_path / "hermes-webui" / "app.py").write_text("print(1)\n", encoding="utf-8")
    return tmp_path


def test_modified_protected_file_is_restored_and_reported(tmp_path):
    ws = _workspace(tmp_path)
    fence = memory_fence.MemoryFence(ws, snapshot_root=tmp_path / "snap")
    fence.arm()
    (ws / "obsidian-vault" / "01-Projects" / "Progetto.md").write_text("stato: B (scritto da un agente)\n", encoding="utf-8")
    report = fence.enforce()
    assert (ws / "obsidian-vault" / "01-Projects" / "Progetto.md").read_text(encoding="utf-8") == "stato: A\n"
    assert report["restored"] == ["obsidian-vault/01-Projects/Progetto.md"]
    assert report["violations"] == 1


def test_created_protected_file_is_removed(tmp_path):
    ws = _workspace(tmp_path)
    fence = memory_fence.MemoryFence(ws, snapshot_root=tmp_path / "snap")
    fence.arm()
    (ws / "obsidian-vault" / "01-Projects" / "Nuova.md").write_text("x", encoding="utf-8")
    (ws / "MEMORY.md").write_text("# memoria\n- riga aggiunta\n", encoding="utf-8")
    report = fence.enforce()
    assert not (ws / "obsidian-vault" / "01-Projects" / "Nuova.md").exists()
    assert (ws / "MEMORY.md").read_text(encoding="utf-8") == "# memoria\n"
    assert set(report["removed"]) == {"obsidian-vault/01-Projects/Nuova.md"}
    assert "MEMORY.md" in report["restored"]


def test_code_outside_the_fence_is_untouched(tmp_path):
    ws = _workspace(tmp_path)
    fence = memory_fence.MemoryFence(ws, snapshot_root=tmp_path / "snap")
    fence.arm()
    (ws / "hermes-webui" / "app.py").write_text("print(2)\n", encoding="utf-8")
    report = fence.enforce()
    assert (ws / "hermes-webui" / "app.py").read_text(encoding="utf-8") == "print(2)\n"
    assert report["violations"] == 0


def test_report_only_dirs_are_reported_not_restored(tmp_path):
    ws = _workspace(tmp_path)
    fence = memory_fence.MemoryFence(ws, snapshot_root=tmp_path / "snap")
    fence.arm()
    (ws / "graphify-out" / "graph.json").write_text('{"changed": true}', encoding="utf-8")
    report = fence.enforce()
    assert (ws / "graphify-out" / "graph.json").read_text(encoding="utf-8") == '{"changed": true}'
    assert report["reported"] == ["graphify-out/graph.json"]


def test_snapshot_is_cleaned_up(tmp_path):
    ws = _workspace(tmp_path)
    fence = memory_fence.MemoryFence(ws, snapshot_root=tmp_path / "snap")
    fence.arm()
    assert any((tmp_path / "snap").iterdir())
    fence.enforce()
    assert not any((tmp_path / "snap").iterdir())


def test_fence_applies_to_everyone_but_librarian():
    assert memory_fence.fence_applies("programmatore") is True
    assert memory_fence.fence_applies("ricercatore") is True
    assert memory_fence.fence_applies("") is True
    assert memory_fence.fence_applies("librarian") is False
    assert memory_fence.fence_applies("memory-librarian") is False


def test_fence_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("HERMES_MEMORY_FENCE", "0")
    assert memory_fence.fence_applies("programmatore") is False


def test_report_renders_a_warning_block():
    text = memory_fence.render_report({"restored": ["obsidian-vault/a.md"], "removed": [], "reported": [], "violations": 1})
    assert "RECINTO MEMORIA" in text
    assert "obsidian-vault/a.md" in text
    assert memory_fence.render_report({"restored": [], "removed": [], "reported": [], "violations": 0}) == ""
