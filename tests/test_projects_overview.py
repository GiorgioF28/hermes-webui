from api import projects_overview


def _vault(tmp_path):
    v = tmp_path / "obsidian-vault"
    (v / "01-Projects").mkdir(parents=True)
    (v / "01-Projects" / "Alpha.md").write_text(
        "# Alpha\n\nintro\n\n## Next\n- [ ] task uno\n- [ ] task due\n- [x] fatto\n\nvedi [[Beta]]\n",
        encoding="utf-8",
    )
    (v / "03-Areas").mkdir()
    (v / "03-Areas" / "Beta.md").write_text("beta", encoding="utf-8")
    return v


def test_overview_tasks_and_latest(tmp_path):
    data = projects_overview.build_projects_overview(_vault(tmp_path))
    assert data["exists"] is True and data["count"] == 1
    p = data["projects"][0]
    assert p["name"] == "Alpha"
    assert p["tasks"] == ["task uno", "task due"]  # open only, [x] excluded, max 3
    names = {l["name"] for l in p["latest"]}
    assert "Alpha" in names and "Beta" in names  # linked note included
    assert len(p["activity"]) == 14


def test_section_priority(tmp_path):
    v = tmp_path / "obsidian-vault"
    (v / "01-Projects").mkdir(parents=True)
    (v / "01-Projects" / "P.md").write_text(
        "- [ ] plain task\n\n## TODO\n- [ ] priority task\n", encoding="utf-8"
    )
    p = projects_overview.build_projects_overview(v)["projects"][0]
    assert p["tasks"][0] == "priority task"  # TODO-section task ranks first


def test_no_projects_dir(tmp_path):
    data = projects_overview.build_projects_overview(tmp_path / "empty")
    assert data["exists"] is False


def test_command_bridge_family_cards_include_concorso_priority_and_clients(tmp_path):
    v = tmp_path / "obsidian-vault"
    (v / "01-Projects").mkdir(parents=True)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "command-bridge-clients.json").write_text(
        '{"cards":{"visionbuilts":[{"name":"Nine","status":"live","waiting_on":"Tom"}]}}',
        encoding="utf-8",
    )
    (v / "01-Projects" / "Concorso INPS Assistente Informatico.md").write_text(
        "# Concorso INPS\n\n## Next\n- [ ] Studiare CAD\n",
        encoding="utf-8",
    )
    (v / "01-Projects" / "Podcast Rap.md").write_text(
        "# Podcast Rap\n\n## Next\n- [ ] Scrivere strofa\n",
        encoding="utf-8",
    )
    (v / "01-Projects" / "VisionBuilts Ebook Platform.md").write_text(
        "# VisionBuilts\n\n## Next\n- [ ] Follow up Nine\n",
        encoding="utf-8",
    )

    families = {row["id"]: row for row in projects_overview.build_projects_overview(v)["families"]}

    assert "concorso-inps" in families
    assert families["concorso-inps"]["name"] == "Concorso INPS"
    assert families["concorso-inps"]["study"]["course"] == "Concorso INPS"
    assert families["rap"]["priority"] == 0
    assert families["visionbuilts"]["clients_active"][0]["name"] == "Nine"
    task = families["visionbuilts"]["tasks"][0]
    assert task["subproject_id"] == "01-projects-visionbuilts-ebook-platform"
    assert task["subproject"] == "VisionBuilts Ebook Platform"
