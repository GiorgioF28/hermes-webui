"""Session-scoped Prime artifact registration and safe byte delivery."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import re
import uuid
from pathlib import Path

from api.config import MAX_UPLOAD_BYTES
from api.upload import _looks_like_secret_name, _session_attachment_dir


def _registry(session_id: str) -> Path:
    return _session_attachment_dir(session_id) / "delivered" / "index.json"


def _read(session_id: str) -> list[dict]:
    try:
        value = json.loads(_registry(session_id).read_text(encoding="utf-8"))
        return value if isinstance(value, list) else []
    except (OSError, ValueError):
        return []


def _public(row: dict) -> dict:
    return {key: row[key] for key in ("id", "name", "size", "mime", "sha256") if key in row}


def register_artifact(session_id: str, path: str, *, name: str | None = None) -> dict:
    """Register a real, bounded file in the Prime workspace or private inbox."""
    from api.routes import _prime_workspace_from_settings
    from api.prime_session_store import get_prime_session_store

    source = Path(path).expanduser().absolute()
    if source.is_symlink() or not source.is_file():
        raise ValueError("Artifact is missing or is a symlink")
    resolved = source.resolve(strict=True)
    sid = str(session_id)
    roots = [_session_attachment_dir(sid).resolve()]
    try:
        roots.append(Path(_prime_workspace_from_settings(get_prime_session_store(sid).get_settings())).resolve())
    except Exception:
        pass
    trusted_workspace_file = any(resolved.is_relative_to(root) for root in roots)
    delegated_file = False
    if not trusted_workspace_file:
        try:
            from api.prime_delegation import get_background_tasks
            delegated_file = any(
                task.get("status") not in {"in_corso", "running", "pending"}
                and task.get("finished")
                and (str(resolved) in str(task.get("output") or "")
                     or resolved.as_posix() in str(task.get("output") or ""))
                for task in get_background_tasks(max_age=float("inf"), session_id=sid)
            )
        except Exception:
            delegated_file = False
    if not trusted_workspace_file and not delegated_file:
        raise ValueError("Artifact must be inside this Prime workspace or attachment inbox")
    if {part.lower() for part in resolved.parts} & {".git", ".ssh", ".hermes"}:
        raise ValueError("Private configuration paths cannot be delivered")
    if resolved.stat().st_size > MAX_UPLOAD_BYTES:
        raise ValueError("Artifact exceeds the configured upload size limit")
    safe = re.sub(r"[^\w.\- ]", "_", name or resolved.name).strip(" .")[:180]
    if not safe or safe in {".", ".."}:
        raise ValueError("Invalid artifact name")
    if _looks_like_secret_name(safe):
        raise ValueError("Credential or key files cannot be delivered")
    data = resolved.read_bytes()
    artifact_id = uuid.uuid4().hex
    artifact = {"id": artifact_id, "name": safe, "size": len(data),
                "mime": mimetypes.guess_type(safe)[0] or "application/octet-stream",
                "sha256": hashlib.sha256(data).hexdigest(), "source_path": str(resolved)}
    if safe.lower().endswith(".zip"):
        import zipfile
        try:
            with zipfile.ZipFile(resolved) as archive:
                names = []
                for info in archive.infolist():
                    member = info.filename.replace("\\", "/")
                    parts = Path(member).parts
                    if member.startswith("/") or ".." in parts or any(_looks_like_secret_name(part) for part in parts):
                        raise ValueError("ZIP contains an unsafe path or private file")
                    mode = (info.external_attr >> 16) & 0o170000
                    if mode == 0o120000:
                        raise ValueError("ZIP symbolic links are not supported")
                    names.append(member)
                if not any(Path(part).name.lower().startswith(("readme", "instructions", "istruzioni"))
                           for part in names):
                    raise ValueError("A site/app ZIP must include README or instructions")
        except zipfile.BadZipFile:
            raise ValueError("The ZIP artifact is invalid") from None
    target = _registry(sid)
    target.parent.mkdir(parents=True, exist_ok=True)
    prior = next((row for row in _read(sid) if row.get("source_path") == str(resolved)
                  and row.get("sha256") == artifact["sha256"]), None)
    if prior:
        return _public(prior)
    rows = _read(sid)
    snapshot = target.parent / f"{artifact_id}-{safe}"
    with snapshot.open("xb") as stream:
        stream.write(data)
    artifact["stored_path"] = str(snapshot.resolve())
    rows.append(artifact)
    target.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    public = _public(artifact)
    url = "/api/bridge/prime/artifacts/" + public["id"]
    get_prime_session_store(sid).inject_assistant_message(
        f"File consegnato: {safe} ({len(data)} byte). Download: {url}",
        {"prime_artifacts": [{**public, "url": url, "open_url": url + "?inline=1"}]},
    )
    return public


def list_artifacts(session_id: str) -> list[dict]:
    return [_public(row)
            for row in _read(session_id) if isinstance(row, dict)]


def artifact_bytes(session_id: str, artifact_id: str) -> tuple[dict, bytes]:
    row = next((item for item in _read(session_id) if item.get("id") == artifact_id), None)
    if not row:
        raise FileNotFoundError("Artifact not found")
    path = Path(str(row.get("stored_path") or ""))
    expected_dir = _registry(session_id).parent.resolve()
    if path.is_symlink() or path.parent.resolve() != expected_dir or not path.name.startswith(artifact_id + "-"):
        raise FileNotFoundError("Artifact file is missing")
    import os
    import stat
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise FileNotFoundError("Artifact file is missing")
            data = stream.read(MAX_UPLOAD_BYTES + 1)
    except OSError:
        raise FileNotFoundError("Artifact file is missing") from None
    if len(data) > MAX_UPLOAD_BYTES:
        raise FileNotFoundError("Artifact file changed after registration")
    if hashlib.sha256(data).hexdigest() != row.get("sha256"):
        raise FileNotFoundError("Artifact changed after registration")
    return row, data
