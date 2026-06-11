from api import vault_graph


def _make_vault(tmp_path):
    v = tmp_path / "obsidian-vault"
    (v / "01-Projects").mkdir(parents=True)
    (v / "01-Projects" / "Alpha.md").write_text("vedi [[Beta]] per i dettagli", encoding="utf-8")
    (v / "06-Agents").mkdir()
    (v / "06-Agents" / "Beta.md").write_text("nota agente", encoding="utf-8")
    (v / "03-Areas").mkdir()
    (v / "03-Areas" / "Note1.md").write_text("una nota", encoding="utf-8")
    (v / "Home.md").write_text("home", encoding="utf-8")
    return v


def test_core_and_families(tmp_path):
    g = vault_graph.build_graph(_make_vault(tmp_path))
    assert g["exists"] is True
    types = [n["type"] for n in g["nodes"]]
    assert "core" in types
    assert any(n["type"] == "project" and n["label"] == "Alpha" for n in g["nodes"])
    assert any(n["type"] == "agent" and n["label"] == "Beta" for n in g["nodes"])
    assert any(n["type"] == "note" and n["label"] == "Note1" for n in g["nodes"])


def test_hierarchy_and_wikilink_edges(tmp_path):
    g = vault_graph.build_graph(_make_vault(tmp_path))
    kinds = {e["kind"] for e in g["edges"]}
    assert "hierarchy" in kinds
    assert "wikilink" in kinds  # Alpha -> Beta resolved by stem
    # core has children
    core = next(n for n in g["nodes"] if n["type"] == "core")
    assert core["childCount"] >= 3


def test_missing_vault(tmp_path):
    g = vault_graph.build_graph(tmp_path / "does-not-exist")
    assert g["exists"] is False
    assert g["nodes"] == []
