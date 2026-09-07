"""Dopo un failover automatico Claude->Codex, il capo torna a Claude da solo
quando la finestra di quota e' passata.

Caso reale (2026-09-07): alle 15:34 Claude ha risposto 429 "resets 3:50pm", il
capo e' passato a Codex (manual=False). La finestra si e' resettata alle 15:50,
ma tutti i turni successivi (16:01, 16:04, 18:37, 18:38) sono rimasti su Codex:
``clear_claude_quota`` scatta solo dopo un turno Claude riuscito, e un turno
Claude non parte mai finche' il capo e' Codex. Il selettore modello (Fable)
sceglie quale Claude usare, non chi comanda: l'utente non aveva modo di capirlo.
"""

from __future__ import annotations

import time

from api import lead_brain


def _failover_to_codex(ws, *, reset_at):
    lead_brain.set_lead(ws, lead_brain.LEAD_CODEX, reason="429", manual=False)
    lead_brain.record_claude_quota(ws, reason="429", reset_at=reset_at)


def test_auto_failover_reverts_to_claude_once_quota_window_passed(tmp_path):
    _failover_to_codex(tmp_path, reset_at=time.time() - 600)
    state = lead_brain.reconcile_lead(tmp_path)
    assert state["lead"] == lead_brain.LEAD_CLAUDE
    assert state["manual"] is False
    assert lead_brain.get_lead(tmp_path) == lead_brain.LEAD_CLAUDE, "deve essere persistito"


def test_auto_failover_stays_on_codex_while_quota_still_exhausted(tmp_path):
    _failover_to_codex(tmp_path, reset_at=time.time() + 3600)
    assert lead_brain.reconcile_lead(tmp_path)["lead"] == lead_brain.LEAD_CODEX


def test_manual_codex_pin_is_never_touched(tmp_path):
    lead_brain.set_lead(tmp_path, lead_brain.LEAD_CODEX, reason="manuale (UI)", manual=True)
    state = lead_brain.reconcile_lead(tmp_path)
    assert state["lead"] == lead_brain.LEAD_CODEX and state["manual"] is True


def test_claude_lead_is_left_alone(tmp_path):
    before = lead_brain.get_lead_state(tmp_path)
    assert lead_brain.reconcile_lead(tmp_path) == before


def test_prime_reply_reconciles_before_routing():
    """routes._hermes_prime_reply deve riconciliare il capo PRIMA di decidere la rotta."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "api" / "routes.py").read_text(encoding="utf-8")
    route = src.index("if lead_brain.get_lead(workspace) == lead_brain.LEAD_CODEX:")
    reconcile = src.rfind("lead_brain.reconcile_lead(workspace)", 0, route)
    assert reconcile != -1, "reconcile_lead non chiamato prima della scelta Claude/Codex"
    assert route - reconcile < 800, "reconcile_lead troppo lontano dalla scelta della rotta"
