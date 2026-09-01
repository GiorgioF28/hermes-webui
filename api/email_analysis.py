"""Batched Daily Brief email analysis with a deterministic fallback."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable


logger = logging.getLogger(__name__)

MAX_ANALYSIS_EMAILS = 80
MAX_SUMMARY_CHARS = 400
MAX_WHY_CHARS = 200
DEFAULT_MODEL = "claude-sonnet-4-6"

_SYSTEM_PROMPT = """Sei Prime e prepari il Daily Brief di Giorgio.
Valuta con severita: la maggior parte delle email e rumore. Usa importanza
\"alta\" solo quando Giorgio deve agire o decidere; usa \"media\" per
informazioni utili ma non urgenti e \"bassa\" per il resto. Rispondi in
italiano con SOLO un array JSON di oggetti {index, importance, summary, why}.
summary deve dire in 1-2 frasi cosa comunica la mail; why deve essere una breve
clausola che spiega perche conta. Non ripetere il nome della casella: il caller
lo mostra separatamente."""


def _default_client() -> Any:
    """Build the already-configured Hermes Anthropic client."""
    from agent.anthropic_adapter import build_anthropic_client
    from hermes_cli.auth import get_anthropic_key

    key = get_anthropic_key()
    if not key:
        raise RuntimeError("Anthropic credential unavailable")
    return build_anthropic_client(key, timeout=120)


def _response_text(response: Any) -> str:
    content = getattr(response, "content", None)
    if content is None and isinstance(response, dict):
        content = response.get("content")
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content if isinstance(content, list) else []:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts).strip()


def _parse_json_array(text: str) -> list[Any]:
    cleaned = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.I | re.S)
    if fenced:
        cleaned = fenced.group(1)
    parsed = json.loads(cleaned)
    if not isinstance(parsed, list):
        raise ValueError("analysis response is not an array")
    return parsed


def _clean_model_text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()[:limit]


def _fallback_rows(
    emails: list[dict[str, Any]],
    *,
    vip_senders: set[str],
    classifier: Callable[..., str],
) -> list[dict[str, str]]:
    return [
        {
            "importance": classifier(email, vip_senders=vip_senders),
            "summary": "",
            "why": "",
        }
        for email in emails
    ]


def analyse_emails(
    emails: list[dict[str, Any]],
    *,
    vip_senders: set[str] | None = None,
    client: Any | None = None,
    client_factory: Callable[[], Any] = _default_client,
    classifier: Callable[..., str] | None = None,
    model: str = DEFAULT_MODEL,
) -> tuple[list[dict[str, str]], str, str | None]:
    """Analyse emails once, returning rows, engine name, and a safe error."""
    if classifier is None:
        from api.daily_brief import classify_importance

        classifier = classify_importance
    vip = vip_senders or set()
    fallback = _fallback_rows(emails, vip_senders=vip, classifier=classifier)
    if not emails:
        return fallback, "rules", None

    batch = emails[:MAX_ANALYSIS_EMAILS]
    prompt_rows = [
        {
            "index": index,
            "account": row.get("account", ""),
            "from": row.get("from", ""),
            "fromName": row.get("fromName", ""),
            "subject": row.get("subject", ""),
            "receivedAt": row.get("receivedAt", ""),
            "bodyExcerpt": row.get("bodyExcerpt", ""),
        }
        for index, row in enumerate(batch)
    ]
    try:
        active_client = client if client is not None else client_factory()
        response = active_client.messages.create(
            model=model,
            max_tokens=8192,
            temperature=0,
            timeout=120,
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": "Analizza queste email:\n" + json.dumps(prompt_rows, ensure_ascii=False),
            }],
        )
        parsed = _parse_json_array(_response_text(response))
        by_index: dict[int, dict[str, Any]] = {}
        for item in parsed:
            if not isinstance(item, dict) or not isinstance(item.get("index"), int):
                continue
            index = item["index"]
            if 0 <= index < len(batch) and index not in by_index:
                by_index[index] = item
        for index in range(len(batch)):
            item = by_index.get(index, {})
            importance = str(item.get("importance") or "").strip().lower()
            if importance in {"alta", "media", "bassa"}:
                fallback[index]["importance"] = importance
            fallback[index]["summary"] = _clean_model_text(item.get("summary"), MAX_SUMMARY_CHARS)
            fallback[index]["why"] = _clean_model_text(item.get("why"), MAX_WHY_CHARS)
        return fallback, "prime", None
    except Exception as exc:
        reason = f"analisi Prime non disponibile ({type(exc).__name__})"[:200]
        logger.warning("daily brief email analysis fallback: %s", reason)
        return fallback, "rules", reason
