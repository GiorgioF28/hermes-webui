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
