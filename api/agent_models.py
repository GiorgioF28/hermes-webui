"""Override manuale del modello per sotto-agente (pannello AGENTI del Command Bridge).

Persistenza: ``<state dir>/agent_models.json`` -> ``{"<slug agente>": "<modello>"}``.
Nessun override = routing storico di ``prime_delegation._model_for`` (Codex di
default). Le voci selezionabili sono le stesse del selettore di Hermes Prime,
piu' ``codex`` (GPT locale) e ``auto``.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from api.config import STATE_DIR

STORE_PATH = STATE_DIR / "agent_models.json"

MODEL_OPTIONS: list[dict[str, str]] = [
    {"id": "auto", "label": "Auto"},
    {"id": "codex", "label": "Codex (GPT)"},
    {"id": "claude-opus-5", "label": "Opus 5"},
    {"id": "claude-sonnet-5", "label": "Sonnet 5"},
    {"id": "claude-haiku-4-5", "label": "Haiku 4.5"},
    {"id": "claude-fable-5-1", "label": "Fable 5.1"},
]
_ALLOWED = {o["id"] for o in MODEL_OPTIONS}
_LABELS = {o["id"]: o["label"] for o in MODEL_OPTIONS}
_lock = threading.Lock()


def label_for(model_id: str) -> str:
    return _LABELS.get(str(model_id or ""), str(model_id or ""))


def _read() -> dict[str, str]:
    try:
        raw = json.loads(Path(STORE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k): str(v)
        for k, v in raw.items()
        if isinstance(v, str) and v in _ALLOWED and v != "auto"
    }


def get_overrides() -> dict[str, str]:
    """Slug agente -> modello scelto a mano. Solo override reali (mai 'auto')."""
    with _lock:
        return _read()


def set_override(agent_id: str, model: str | None) -> dict[str, str]:
    """Imposta (o toglie, con 'auto'/vuoto) l'override di un agente. Ritorna la mappa."""
    slug = str(agent_id or "").strip()
    if not slug:
        raise ValueError("agent_id is required")
    choice = str(model or "auto").strip() or "auto"
    if choice not in _ALLOWED:
        raise ValueError(f"unknown model {choice!r}; allowed: {sorted(_ALLOWED)}")
    with _lock:
        current = _read()
        if choice == "auto":
            current.pop(slug, None)
        else:
            current[slug] = choice
        path = Path(STORE_PATH)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, path)
        return dict(current)
