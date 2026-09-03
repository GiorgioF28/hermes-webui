"""Source-contract del pannello AGENTI nel Command Bridge."""

from __future__ import annotations

from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / "static" / "command_bridge.js").read_text(encoding="utf-8")


def _render_agents_body():
    body = SRC[SRC.index("function renderAgents("):]
    return body[:body.index("var _worklogData")]


def test_only_running_agents_are_green():
    body = _render_agents_body()
    assert "'vivo'" not in body, "vivo (usato di recente) non deve piu' accendere il pallino"
    assert "if (s === 'attivo') return 'cb-live';" in body


def test_agent_row_has_model_select_wired_to_endpoint():
    assert 'class="cb-agent-model"' in SRC
    assert "data-agent-id=" in SRC
    # Le option sono generate da AGENT_MODEL_OPTIONS: le sei voci devono stare
    # in quell'array, nello stesso vocabolario che il server valida.
    options_src = SRC[SRC.index("var AGENT_MODEL_OPTIONS = ["):]
    options_src = options_src[:options_src.index("];")]
    for value in ("auto", "codex", "claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5", "claude-fable-5-1"):
        assert "['" + value + "'," in options_src, value
    assert "api/bridge/agents/model" in SRC


def test_panel_refreshes_on_the_same_signal_as_planets():
    body = SRC[SRC.index("function syncStarActiveAgents("):]
    body = body[:body.index("_cbActiveAgents = next;")]
    assert "pollAgents()" in body, "quando cambia l'insieme degli agenti attivi il pannello si aggiorna subito"
    marker = "setInterval(pollAgents, "
    start = SRC.index(marker) + len(marker)
    digits = ""
    while SRC[start].isdigit():
        digits += SRC[start]
        start += 1
    assert int(digits) <= 5000, "polling agenti troppo lento rispetto ai pianeti (3s)"


def test_render_does_not_clobber_an_open_select():
    assert "document.activeElement" in _render_agents_body()
