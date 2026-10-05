"""Cold-start activation and checked retrieval for an operator-staged archive.

The caller owns the Prime store lock and must have recovered stale turns before
activation. No running server endpoint can archive or restore the live store.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

from api.prime_history_archive import ArchiveError, apply_archive_atomically, build_archive_bundle, write_json_atomic


def _write_bytes_atomic(path: Path, content: bytes) -> None:
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile("wb", dir=path.parent, prefix=path.name + ".tmp.", delete=False) as stream:
            temp_name = stream.name
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        temp_name = None
    finally:
        if temp_name and os.path.exists(temp_name):
            os.unlink(temp_name)


def _read_bundle(metadata: dict) -> dict:
    content = Path(metadata["bundle_path"]).read_bytes()
    if hashlib.sha256(content).hexdigest() != metadata["bundle_sha256"]:
        raise ArchiveError("archive checksum mismatch")
    bundle = json.loads(content)
    if bundle.get("source_sha256") != metadata["archive_id"]:
        raise ArchiveError("archive identity mismatch")
    return bundle


def archived_messages(metadata: dict, offset: int, limit: int) -> dict[int, dict]:
    bundle = _read_bundle(metadata)
    return {row["index"]: row["message"] for row in bundle["archived_entries"]
            if offset <= row["index"] < offset + limit}


def activate_request(store, data: dict, request_path: Path) -> None:
    """Archive current disk state, never a stale dry-run snapshot."""
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if request.get("schema") != 1 or request.get("session_id") != store.session_id:
        raise ArchiveError("archive request does not match this session")
    if data.get("pending_turn") or store.has_buffered_partial():
        raise ArchiveError("cannot archive while a turn is active")
    summary = str(request.get("summary") or "").strip()
    if not summary or len(summary) > 6000:
        raise ArchiveError("archive request requires a bounded summary")
    cutoff = request["cutoff"]
    existing = data.get("_prime_archive")
    if existing:
        from api.prime_history_archive import parse_cutoff
        if parse_cutoff(existing["cutoff"]) != parse_cutoff(cutoff):
            raise ArchiveError("a different archive is already active")
        _read_bundle(existing)
    else:
        source_bytes = store.path.read_bytes()
        bundle, candidate = build_archive_bundle(source_bytes, cutoff, summary=summary)
        archive_dir = store.path.parent / "_prime_archives" / bundle["source_sha256"]
        archive_dir.mkdir(parents=True, exist_ok=True)
        backup_path = archive_dir / "original.json"
        bundle_path = archive_dir / "archive.json"
        _write_bytes_atomic(backup_path, source_bytes)
        write_json_atomic(bundle_path, bundle)
        bundle_hash = hashlib.sha256(bundle_path.read_bytes()).hexdigest()
        candidate["_prime_archive"].update({
            "summary": summary, "bundle_path": str(bundle_path), "bundle_sha256": bundle_hash,
            "original_path": str(backup_path),
            "brief_indexes": {str(row["message"]["brief_id"]): row["index"]
                              for row in bundle["archived_entries"] if row["message"].get("brief_id")},
        })
        manifest = {key: bundle[key] for key in (
            "schema", "cutoff", "source_sha256", "source_bytes", "message_count_before",
            "archived_count", "preserved_count")}
        manifest.update({"bundle_sha256": bundle_hash, "backup": backup_path.name,
                         "rollback": "Stop the server; restore slots with restore_archived_messages; preserve newer turns."})
        write_json_atomic(archive_dir / "manifest.json", manifest)
        # Archive, full original backup and manifest exist before state commits.
        # Do not touch updated_at: maintenance is not conversation activity.
        apply_archive_atomically(store.path, bundle["source_sha256"], candidate, active_turn=False)
    os.replace(request_path, request_path.with_suffix(".applied.json"))
