"""Prime session history sanitizer for model-facing transcript reconstruction.

Reuses the strategy from api.streaming._sanitize_messages_for_api but adapted
for the Prime store message format (user/assistant only, no tool roles).

P3-A (bridge-parity-p2): applied when rebuilding Prime transcript for the
model after compact/recovery so the model never sees error markers or
content-free partial messages.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Max chars per assistant message before truncating (prevents context bloat
# from very large code dumps while keeping the gist visible).
_MAX_ASSISTANT_CHARS = 50_000
_TRUNCATE_SUFFIX = "\n\n[... troncato per lunghezza — contesto precedente preservato]"


def sanitize_prime_history_for_model(messages: list[dict]) -> list[dict]:
    """Return a clean copy of Prime messages safe to send to the model.

    Rules applied (in order):
    1. Non-dict entries are dropped.
    2. Messages with no meaningful content (empty string after strip) are dropped
       unless they carry attachment metadata (attachments list non-empty).
    3. Messages marked as error with no partial content (interrupted=True AND
       content empty after strip) are dropped — these are noise to the model.
    4. Very long assistant content is truncated at _MAX_ASSISTANT_CHARS to
       prevent runaway context growth from large code-dump responses.
    5. Only the keys relevant to model context are forwarded: role, content.
       Internal metadata (created_at, usage, interrupted, cancelled, error,
       attachments, recovered) is stripped.
    """
    if not messages:
        return []
    clean: list[dict] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "").strip()
        if role not in ("user", "assistant"):
            continue
        content = str(msg.get("content") or "").strip()
        has_attachments = bool(msg.get("attachments"))
        # Drop empty messages without attachments (partial/error with no output).
        if not content and not has_attachments:
            continue
        # Drop error-only partial messages (interrupted with no content).
        if msg.get("interrupted") and not content and not has_attachments:
            continue
        # Truncate very long assistant output to prevent context bloat.
        if role == "assistant" and len(content) > _MAX_ASSISTANT_CHARS:
            content = content[:_MAX_ASSISTANT_CHARS] + _TRUNCATE_SUFFIX
        entry: dict[str, Any] = {"role": role, "content": content}
        clean.append(entry)
    return clean
