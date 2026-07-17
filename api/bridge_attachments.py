"""Command Bridge attachment helpers.

Prime keeps a persistent SDK session. Attachments enter that session as short
text handles; files stay on disk for on-demand Read access.
"""
from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

IMAGE_RETAIN_TURNS = 2

_PRIME_TURN = 0
_PRIME_IMAGE_ATTACHMENTS: list[dict[str, Any]] = []


def attachment_label(filename: str) -> str:
    return (Path(filename or "documento").stem.replace("_", " ").replace("-", " ").strip() or "documento")[:80]


def is_pdf_mime(filename: str, mime: str = "") -> bool:
    guessed = mimetypes.guess_type(filename or "")[0] or ""
    value = (mime or guessed).split(";", 1)[0].lower()
    return value == "application/pdf" or str(filename or "").lower().endswith(".pdf")


def prime_turn_started() -> int:
    global _PRIME_TURN
    _PRIME_TURN += 1
    return _PRIME_TURN


def normalize_prime_attachments(attachments, *, bridge: str = "hermes-prime") -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in attachments or []:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path:
            continue
        name = str(item.get("name") or item.get("filename") or Path(path).name).strip()
        mime = str(item.get("mime") or item.get("type") or mimetypes.guess_type(name)[0] or "").strip()
        att = dict(item)
        att["name"] = name or Path(path).name
        att["path"] = path
        att["mime"] = mime
        if is_pdf_mime(att["name"], mime):
            att["kind"] = "pdf"
            att.setdefault("label", attachment_label(att["name"]))
            att.setdefault("summary", "PDF salvato su disco: usa Read sul path indicato per dettagli puntuali.")
        elif mime.startswith("image/"):
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
    blocks: list[str] = []
    pdf_lines: list[str] = []
    image_lines: list[str] = []
    file_lines: list[str] = []
    for att in attachments or []:
        if not isinstance(att, dict):
            continue
        path = str(att.get("path") or "").strip()
        if not path:
            continue
        if att.get("kind") == "pdf":
            label = str(att.get("label") or attachment_label(att.get("name") or path))
            summary = str(att.get("summary") or "").strip()
            pdf_lines.append(
                f'[Allegato PDF "{label}" - path: {path} - riassunto: {summary}. '
                "Per dettagli usa Read sul path.]"
            )
        elif att.get("kind") == "image":
            image_lines.append(
                f'- immagine "{att.get("name") or Path(path).name}" - path: {path} '
                "(disponibile con Read; dopo 2 turni resta solo placeholder testuale)"
            )
        else:
            file_lines.append(f"- {path}")
    if pdf_lines:
        blocks.append("PDF allegati in questo turno:\n" + "\n".join(pdf_lines))
    if image_lines:
        blocks.append("Immagini allegate in questo turno:\n" + "\n".join(image_lines))
    if file_lines:
        blocks.append("File allegati in questo turno, leggili con Read se rilevanti:\n" + "\n".join(file_lines))
    stale = prime_stale_image_placeholders(current_turn=current_turn, retain_turns=retain_turns)
    if stale:
        blocks.append("Promemoria allegati storici non reiniettati:\n" + "\n".join(f"- {x}" for x in stale))
    return "\n\n" + "\n\n".join(blocks) if blocks else ""
