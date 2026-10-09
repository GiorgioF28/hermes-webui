from __future__ import annotations

import importlib.util
from pathlib import Path


_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_daily_brief_workflow_patch.py"
_SPEC = importlib.util.spec_from_file_location("daily_brief_workflow_patch", _SCRIPT)
_MODULE = importlib.util.module_from_spec(_SPEC)
assert _SPEC and _SPEC.loader
_SPEC.loader.exec_module(_MODULE)


def _workflow():
    names = {
        "Gmail personale": "n8n-nodes-base.gmail",
        "Normalize email rows": "n8n-nodes-base.code",
        "POST email digest": "n8n-nodes-base.httpRequest",
        "POST email accumulate": "n8n-nodes-base.httpRequest",
        "Label Gmail secondario": "n8n-nodes-base.set",
        "Label Yahoo": "n8n-nodes-base.set",
        "Schedule 07:00 Europe Rome": "n8n-nodes-base.scheduleTrigger",
        "POST check DM": "n8n-nodes-base.httpRequest",
    }
    nodes = []
    for name, kind in names.items():
        node = {"id": name, "name": name, "type": kind, "parameters": {}, "position": [0, 0]}
        if name == "Gmail personale":
            node["onError"] = "continueRegularOutput"
        if name.startswith("POST "):
            node["parameters"] = {"url": "old", "body": "old", "options": {}}
            node["credentials"] = {"httpHeaderAuth": {"id": "credential-ref", "name": "Hermes Cron Token"}}
        nodes.append(node)
    return {
        "id": "HermesDailyBriefV2", "name": "Hermes Daily Brief", "active": True,
        "nodes": nodes,
        "connections": {
            "Schedule 07:00 Europe Rome": {"main": [[{"node": "POST check DM"}, {"node": "Gmail personale"}]]},
            "Normalize email rows": {"main": [[{"node": "POST email digest"}]]},
            "Gmail personale": {"main": [[{"node": "Normalize email rows"}]]},
            "Label Gmail secondario": {"main": [[{"node": "POST email accumulate"}]]},
            "Label Yahoo": {"main": [[{"node": "POST email accumulate"}]]},
        },
    }


def test_patch_separates_gmail_intake_from_recap_and_keeps_auth_reference():
    result = _MODULE.prepare(_workflow())
    names = {node["name"] for node in result["nodes"]}
    assert "POST email accumulate Gmail" in names
    assert "POST daily recap" in names
    assert "Normalize IMAP rows" in names
    assert "POST email digest" not in names
    by_name = {node["name"]: node for node in result["nodes"]}
    assert by_name["POST email accumulate Gmail"]["parameters"]["url"].endswith("email-accumulate")
    assert by_name["POST daily recap"]["parameters"]["url"].endswith("/email")
    assert by_name["POST daily recap"]["credentials"] == {"httpHeaderAuth": {"id": "credential-ref", "name": "Hermes Cron Token"}}
    assert by_name["POST daily recap"]["retryOnFail"] is True
    assert result["connections"]["POST email accumulate Gmail"]["main"][0][0]["node"] == "POST daily recap"
    assert result["connections"]["Normalize IMAP rows"]["main"][0][0]["node"] == "POST email accumulate"
    assert "Number.isFinite(date.getTime())" in by_name["Normalize email rows"]["parameters"]["jsCode"]
    assert "const bodyText = textBody || htmlBody" in by_name["Normalize email rows"]["parameters"]["jsCode"]


def test_patch_refuses_unexpected_or_inactive_workflow():
    invalid = _workflow()
    invalid["active"] = False
    try:
        _MODULE.prepare(invalid)
    except ValueError as exc:
        assert "not active" in str(exc)
    else:
        raise AssertionError("inactive workflow should require explicit review")
