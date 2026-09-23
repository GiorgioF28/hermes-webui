"""Bounded, replay-safe presentation of delegation outcomes (no provider calls)."""
from __future__ import annotations

import re

DIAGNOSTIC_LIMIT = 12_000
ERROR_LIMIT = 500
_QUOTA = re.compile(r"hit your usage limit|usage_limit_(?:exceeded|reached)|plan limit reached|used up your usage|insufficient_quota", re.I)


def diagnostic_excerpt(text: str) -> str:
    text = str(text or "")
    if len(text) <= DIAGNOSTIC_LIMIT:
        return text
    marker = f"\n[… estratto del log: {len(text)} caratteri originali …]\n"
    return text[:2000] + marker + text[-(DIAGNOSTIC_LIMIT - 2000 - len(marker)):]


def is_transcript(text: str) -> bool:
    return bool(re.match(r"(?:Codex CLI exit \d+: )?OpenAI Codex v", str(text or "").lstrip()))


def process_failure(detail: str) -> tuple[str, str]:
    """Use terminal CLI error lines, never quota words inside an echoed task."""
    text = str(detail or "").strip()
    # The human CLI transcript ends with ERROR lines, optional shutdown noise,
    # and its token counter. Command output/prompt mentions are not evidence.
    tail = re.sub(r"\ntokens used\s+[^\n]+\s*$", "", text).rstrip()
    lines = tail.splitlines()
    terminal = []
    for line in reversed(lines):
        if line.startswith("ERROR:"):
            terminal.append(line)
        elif re.match(r"\d{4}-\d\d-\d\dT.* ERROR .*", line):
            continue
        elif line.strip():
            break
    evidence = "\n".join(terminal) if terminal else (text if len(text) <= ERROR_LIMIT and not is_transcript(text) else "")
    if _QUOTA.search(evidence):
        reset = re.search(r"try again at ([^\n]+)", evidence, re.I)
        message = "Limite di utilizzo Codex raggiunto."
        if reset:
            message += " Riprova: " + reset.group(1).strip()[:160]
        return "quota_exhausted", message
    message = terminal[0] if terminal else evidence
    return "process_exit", (message or "Codex si è interrotto prima del risultato finale.")[:ERROR_LIMIT]


def normalise_outcome(task: dict) -> dict:
    """Copy a legacy view; keep status/anchors and separate result from trace."""
    t = dict(task)
    result = t.get("result") or {}
    error = t.get("error") or {}
    output = str(t.get("output") or result.get("text") or "")
    failure = str(t.get("failure_reason") or error.get("message") or "")
    category = str(t.get("error_category") or error.get("category") or "")
    diagnostic = str(t.get("diagnostic_log") or result.get("diagnostic_log") or "")
    if is_transcript(output):
        diagnostic = diagnostic or (failure if is_transcript(failure) else output)
        output = ""
    if failure and (is_transcript(failure) or category == "process_exit"):
        parsed_category, message = process_failure(failure)
        diagnostic = diagnostic or failure
        category, failure = parsed_category, message
    t.update(output=output, failure_reason=failure[:ERROR_LIMIT], error_category=category,
             diagnostic_log=diagnostic_excerpt(diagnostic),
             result_partial=bool(t.get("result_partial") or result.get("partial") or t.get("status") == "parziale"))
    t["summary"] = outcome_summary(t)
    # Historical rows can contain both schemas; clear nested unbounded copies.
    if "result" in t:
        t["result"] = {**result, "text": output, "diagnostic_log": t["diagnostic_log"]}
    if "error" in t:
        t["error"] = {**error, "message": t["failure_reason"], "category": category}
    return t


def outcome_summary(t: dict) -> str:
    status = str(t.get("status") or "")
    if status in {"in_corso", "running", "pending"}:
        state = "in corso"
    elif status in {"ok", "done", "parziale"}:
        state = "parziale" if t.get("result_partial") or status == "parziale" else "completato"
    else:
        state = "interrotta" if t.get("result_partial") else "fallita"
    if t.get("error_category") == "quota_exhausted":
        state += " — quota esaurita"
    subject = " ".join(str(t.get("task") or t.get("task_type") or "delega").split())
    # Only a labelled commit in a result, never a date or session UUID in a log.
    output = str(t.get("output") or "")
    match = None if is_transcript(output) else re.search(r"\bcommit\s*:\s*`?([0-9a-f]{7,40})(?![0-9a-f-])\b|\bcommit\s+`?([0-9a-f]{7,40})(?![0-9a-f-])\b", output, re.I)
    commit = ", commit " + (match.group(1) or match.group(2))[:12] if match else ""
    return f"{t.get('id', '')} - {subject[:72]}: {state}{commit}"[:140]


def enrich_history_outcomes(cards: list[dict], records: list[dict], session_id: str) -> list[dict]:
    """Join only existing cards in this session; never create or replay a brief."""
    indexed = {str(r.get("id")): r for r in records
               if str((r.get("ui") or {}).get("anchor_session_id") or r.get("session_id") or "hermes-prime") == session_id}
    enriched = []
    for card in cards:
        record = indexed.get(str(card.get("id")))
        if record is None:
            enriched.append(dict(card))
            continue
        view = normalise_outcome(record)
        enriched.append({**card, **{k: view[k] for k in (
            "output", "failure_reason", "error_category", "diagnostic_log", "result_partial", "summary")}})
    return enriched
