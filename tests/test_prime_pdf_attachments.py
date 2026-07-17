import json
from pathlib import Path


def test_prime_pdf_ingest_writes_index_and_summary(tmp_path, monkeypatch):
    from api import bridge_attachments as ba
    import api.upload as upload

    monkeypatch.setattr(upload, "_attachment_root", lambda: tmp_path.resolve())
    monkeypatch.setattr(ba, "_extract_pdf_text", lambda path: ("Executive summary. Key result is visible. Next action is review.", 3))

    pdf = tmp_path / "hermes-prime" / "Business Plan.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.4 fake")

    entry = ba.ingest_pdf_attachment(pdf, bridge="hermes-prime")

    assert entry["label"] == "Business Plan"
    assert entry["pages"] == 3
    assert "Key result" in entry["summary"]
    assert (pdf.with_name(pdf.name + ".summary.md")).exists()
    index_rows = json.loads((pdf.parent / "index.json").read_text(encoding="utf-8"))
    assert index_rows[0]["path"] == str(pdf)
    assert "%PDF" not in json.dumps(index_rows)


def test_prime_attachment_note_keeps_pdf_summary_only(tmp_path, monkeypatch):
    from api import bridge_attachments as ba
    import api.upload as upload

    monkeypatch.setattr(upload, "_attachment_root", lambda: tmp_path.resolve())
    monkeypatch.setattr(ba, "_extract_pdf_text", lambda path: ("A compact PDF summary.", 2))

    pdf = tmp_path / "hermes-prime" / "report.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF binary bytes that must not enter prompt")

    attachments = ba.normalize_prime_attachments([{"name": "report.pdf", "path": str(pdf), "mime": "application/pdf"}])
    note = ba.build_prime_attachment_note(attachments, current_turn=1)

    assert '[Allegato PDF "report"' in note
    assert "path:" in note
    assert "A compact PDF summary." in note
    assert "binary bytes" not in note
    assert "specificando pages" in note


def test_prime_image_placeholder_after_two_turns():
    from api import bridge_attachments as ba

    ba._PRIME_IMAGE_ATTACHMENTS.clear()
    ba.record_prime_images(
        [{"kind": "image", "name": "screen.png", "path": str(Path("attachments/hermes-prime/screen.png"))}],
        turn=1,
    )

    assert ba.prime_stale_image_placeholders(current_turn=2) == []
    placeholders = ba.prime_stale_image_placeholders(current_turn=3)
    assert len(placeholders) == 1
    assert "screen.png" in placeholders[0]
    assert "Read" in placeholders[0]
