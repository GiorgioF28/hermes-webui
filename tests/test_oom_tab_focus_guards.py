"""Guardie contro l'OOM al ritorno in primo piano su sessioni lunghe (upstream #7006/#6999).

Due delle quattro parti del fix upstream si applicano al nostro fork:

1. `_ensureMessagesLoaded` allargava la finestra di render a TUTTA la
   transcript caricata a ogni force reload (focus tab, catch-up): su sessioni
   lunghe azzerava il conteggio "nascosti prima" e allargava il render non
   virtualizzato. Ora la crescita e' limitata a 4x la finestra di default.
2. `refreshActiveSessionIfExternallyUpdated` partiva anche se un load della
   stessa sessione era gia' in volo: doppio fetch della transcript intera +
   doppio renderMessages = il pattern OOM. Ora, se `_loadingSessionId` e' la
   sessione attiva, la probe salta.

Le altre due (coalescing dei frame `session-updated`, hash bounded della
cache HTML) toccano codice che il fork non ha.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"


def _fn_body(src: str, header: str, end_marker: str) -> str:
    start = src.index(header)
    return src[start:src.index(end_marker, start)]


def test_ensure_messages_loaded_caps_render_window_growth():
    src = (STATIC / "sessions.js").read_text(encoding="utf-8")
    body = _fn_body(src, "async function _ensureMessagesLoaded(", "\nasync function ")
    assert "_messageRenderWindowSize=Math.max(_currentMessageRenderWindowSize(), _messageRenderableMessageCount());" not in body, \
        "crescita illimitata della finestra di render a ogni force reload"
    assert "Math.min(_messageRenderableMessageCount()" in body
    assert "MESSAGE_RENDER_WINDOW_DEFAULT" in body and "*4" in body.replace(" ", "")


def test_external_refresh_skips_when_same_session_load_in_flight():
    src = (STATIC / "sessions.js").read_text(encoding="utf-8")
    body = _fn_body(src, "async function refreshActiveSessionIfExternallyUpdated(", "_activeSessionExternalRefreshInFlight = true;")
    assert "_loadingSessionId === S.session.session_id" in body, \
        "la probe deve saltare se un load della stessa sessione e' gia' in volo"


def test_user_initiated_expansions_are_untouched():
    """Le espansioni volute dall'utente (salta all'inizio / alla domanda) restano intere."""
    src = (STATIC / "ui.js").read_text(encoding="utf-8")
    assert src.count("_messageRenderWindowSize=Math.max(_currentMessageRenderWindowSize(),_messageRenderableMessageCount());") == 2
