"""Command Bridge attachment helpers.

Prime keeps a persistent Claude SDK session, so attachments must enter that
session as small text handles. The original files stay on disk for Read-tool
access when the user asks for exact details.
"""
from __future__ import annotations

import json
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from api.config import MAX_UPLOAD_BYTES
from api.upload import _session_attachment_dir

PDF_MAX_BYTES = min(MAX_UPLOAD_BYTES, 30 * 1024 * 1024)
PDF_SUMMARY_CHARS = 1800
PDF_EXTRACT_CHARS = 12000
IMAGE_RETAIN_TURNS = 2

_PRIME_TURN = 0
_PRIME_IMAGE_ATTACHMENTS: list[dict[str, Any]] = []


def attachment_label(filename: str) -> str:
    stem = Path(filename or "documento").stem
    label = re.sub(r"[_\-]+", " ", stem).strip()
    label = re.sub(r"\s+", " ", label)
    return (label or "documento")[:80]


def is_pdf_mime(filename: str, mime: str = "") -> bool:
    guessed = mimetypes.guess_type(filename or "")[0] or ""
    return (mime or guessed).split(";", 1)[0].lower() == "application/pdf" or str(filename).lower().endswith(".pdf")


def _index_path(bridge: str) -> Path:
    return _session_attachment_dir(bridge) / "index.json"


def _read_index(bridge: str) -> list[dict[str, Any]]:
    path = _index_path(bridge)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_index(bridge: str, rows: list[dict[str, Any]]) -> None:
    path = _index_path(bridge)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_pdf_text(path: Path, *, max_chars: int = PDF_EXTRACT_CHARS) -> tuple[str, int | None]:
    """Best-effort PDF text extraction without adding a hard dependency."""
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception:
        PdfReader = None
    if PdfReader is not None:
        try:
            reader = PdfReader(str(path))
            pages = len(reader.pages)
            chunks: list[str] = []
            for page in reader.pages[:6]:
                text = page.extract_text() or ""
                if text.strip():
                    chunks.append(text)
                if sum(len(c) for c in chunks) >= max_chars:
                    break
            return _clean_text("\n".join(chunks))[:max_chars], pages
        except Exception:
            pass

    # Fallback for environments without pypdf: pull readable strings. This is
    # intentionally bounded and never tries to feed binary PDF data to a model.
    try:
        raw = path.read_bytes()[: max_chars * 4]
    except OSError:
        return "", None
    strings = re.findall(rb"[\x20-\x7e]{5,}", raw)
    return _clean_text(" ".join(s.decode("latin-1", errors="ignore") for s in strings))[:max_chars], None


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _summarize_text_extract(label: str, text: str) -> str:
    text = _clean_text(text)
    if not text:
        return (
            "Testo non estraibile automaticamente. Il PDF e salvato su disco: "
            "per dettagli puntuali usa Read sul path indicato, specificando le pagine."
        )
    sentences = re.split(r"(?<=[.!?])\s+", text)
    picked: list[str] = []
    for sentence in sentences:
        s = sentence.strip()
        if len(s) < 25:
            continue
        picked.append(s)
        if len(" ".join(picked)) >= PDF_SUMMARY_CHARS:
            break
    summary = " ".join(picked) or text[:PDF_SUMMARY_CHARS]
    if len(summary) > PDF_SUMMARY_CHARS:
        summary = summary[: PDF_SUMMARY_CHARS - 1].rstrip() + "..."
    return summary or f"PDF '{label}' salvato; contenuto da recuperare on-demand con Read."


def ingest_pdf_attachment(path: str | Path, *, bridge: str = "hermes-prime", filename: str = "") -> dict[str, Any]:
    pdf_path = Path(path).expanduser().resolve()
    label = attachment_label(filename or pdf_path.name)
    text, pages = _extract_pdf_text(pdf_path)
    summary = _summarize_text_extract(label, text)
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "file": pdf_path.name,
        "path": str(pdf_path),
        "label": label,
        "summary": summary,
        "pages": pages,
        "date": now,
        "mime": "application/pdf",
        "size": pdf_path.stat().st_size if pdf_path.exists() else 0,
    }
    summary_path = pdf_path.with_name(pdf_path.name + ".summary.md")
    summary_path.write_text(
        f"# {label}\n\nPath: `{pdf_path}`\n\nPagine: {pages or 'sconosciute'}\n\n{summary}\n",
        encoding="utf-8",
    )
    rows = [r for r in _read_index(bridge) if not (isinstance(r, dict) and r.get("path") == str(pdf_path))]
    rows.append(entry)
    _write_index(bridge, rows)
    entry["summary_path"] = str(summary_path)
    return entry


def prime_turn_started() -> int:
    global _PRIME_TURN
    _PRIME_TURN += 1
    return _PRIME_TURN


def normalize_prime_attachments(attachments, *, bridge: str = "hermes-prime") -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("filename") or "").strip()
        path = str(item.get("path") or "").strip()
        mime = str(item.get("mime") or item.get("type") or "").strip()
        if not path:
            continue
        att = dict(item)
        att["name"] = name or Path(path).name
        att["path"] = path
        att["mime"] = mime or (mimetypes.guess_type(att["name"])[0] or "")
        if is_pdf_mime(att["name"], att["mime"]):
            if not att.get("summary"):
                try:
                    att.update(ingest_pdf_attachment(path, bridge=bridge, filename=att["name"]))
                except Exception:
                    att["label"] = attachment_label(att["name"])
                    att["summary"] = "PDF salvato, ma il sommario automatico non e riuscito. Usa Read sul path per recupero puntuale."
                    att["pages"] = None
            att["kind"] = "pdf"
        elif str(att.get("mime") or "").startswith("image/"):
            att["kind"] = "image"
        normalized.append(att)
    return normalized


def record_prime_images(attachments, *, turn: int) -> None:
    for att in attachments or []:
        if not isinstance(att, dict) or att.get("kind") != "image":
            continue
        path = str(att.get("path") or "").strip()
        if not path:
            continue
        _PRIME_IMAGE_ATTACHMENTS.append({
            "turn": turn,
            "name": str(att.get("name") or Path(path).name),
            "path": path,
        })
    # Keep only a small operational ledger; files remain on disk.
    del _PRIME_IMAGE_ATTACHMENTS[:-50]


def prime_stale_image_placeholders(*, current_turn: int, retain_turns: int = IMAGE_RETAIN_TURNS) -> list[str]:
    rows: list[str] = []
    seen: set[str] = set()
    for att in _PRIME_IMAGE_ATTACHMENTS:
        path = str(att.get("path") or "")
        if not path or path in seen:
            continue
        if current_turn - int(att.get("turn") or 0) >= retain_turns:
            seen.add(path)
            rows.append(
                f'[immagine "{att.get("name")}" rimossa dal contesto attivo: '
                f"rileggila da {path} con Read se serve]"
            )
    return rows[-8:]


def build_prime_attachment_note(attachments, *, current_turn: int, retain_turns: int = IMAGE_RETAIN_TURNS) -> str:
    if not attachments:
        stale = prime_stale_image_placeholders(current_turn=current_turn, retain_turns=retain_turns)
        if stale:
            return "\n\nPromemoria allegati storici non reiniettati:\n" + "\n".join(f"- {x}" for x in stale)
        return ""

    pdf_lines: list[str] = []
    image_lines: list[str] = []
    file_lines: list[str] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        path = str(att.get("path") or "").strip()
        if not path:
            continue
        if att.get("kind") == "pdf":
            label = str(att.get("label") or attachment_label(att.get("name") or path))
            pages = att.get("pages") or "pagine sconosciute"
            summary = _clean_text(str(att.get("summary") or ""))
            pdf_lines.append(
                f'[Allegato PDF "{label}" ({pages}) - path: {path} - '
                f"riassunto: {summary}. Per dettagli leggi il PDF col tool Read "
                "specificando pages, o delega un estrattore puntuale.]"
            )
        elif att.get("kind") == "image":
            image_lines.append(
                f'- immagine "{att.get("name") or Path(path).name}" - path: {path} '
                "(disponibile con Read; dopo 2 turni resta solo placeholder testuale)"
            )
        else:
            file_lines.append(f"- {path}")
    blocks: list[str] = []
    if pdf_lines:
        blocks.append("PDF allegati in questo turno (solo label/path/riassunto in history):\n" + "\n".join(pdf_lines))
    if image_lines:
        blocks.append("Immagini allegate in questo turno:\n" + "\n".join(image_lines))
    if file_lines:
        blocks.append("File allegati in questo turno, leggili con Read se rilevanti:\n" + "\n".join(file_lines))
    stale = prime_stale_image_placeholders(current_turn=current_turn, retain_turns=retain_turns)
    if stale:
        blocks.append("Promemoria allegati storici non reiniettati:\n" + "\n".join(f"- {x}" for x in stale))
    return "\n\n" + "\n\n".join(blocks) if blocks else ""
