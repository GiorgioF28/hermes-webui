"""Prime file exchange stays bounded, authenticated and session scoped."""
import hashlib
import io
import json
from pathlib import Path

import pytest
from tests.test_prime_brief_dedup import browser  # noqa: F401
from tests.test_prime_image_upload import scene


class _UploadHandler:
    def __init__(self, name, data):
        self.raw = (b"--unit\r\nContent-Disposition: form-data; name=\"file\"; filename=\"" +
                    name.encode() + b"\"\r\nContent-Type: application/octet-stream\r\n\r\n" + data +
                    b"\r\n--unit--\r\n")
        self.headers = {"Content-Type": "multipart/form-data; boundary=unit",
                        "Content-Length": str(len(self.raw))}
        self.rfile = io.BytesIO(self.raw)


def test_prime_upload_accepts_document_and_rejects_active_svg(tmp_path, monkeypatch):
    from api import upload
    from api.identity import HermesIdentity
    monkeypatch.setattr(upload, "_attachment_root", lambda: tmp_path.resolve())
    monkeypatch.setattr("api.identity.resolve_request_identity", lambda handler: HermesIdentity("giorgio", "prime-a"))
    answers = []
    monkeypatch.setattr(upload, "j", lambda handler, body, status=200: answers.append((status, body)))
    upload.handle_prime_upload(_UploadHandler("report.txt", b"bounded input"))
    assert answers[-1][0] == 200
    assert Path(answers[-1][1]["path"]).read_bytes() == b"bounded input"
    upload.handle_prime_upload(_UploadHandler("active.svg", b"<svg><script>alert(1)</script></svg>"))
    assert answers[-1][0] == 400
    assert "non supportato" in answers[-1][1]["error"]
    monkeypatch.setattr(upload, "MAX_UPLOAD_BYTES", 5)
    upload.handle_prime_upload(_UploadHandler("large.txt", b"more than five bytes"))
    assert answers[-1][0] == 413


@pytest.mark.parametrize("filename", ["auth.json", "auth.json ", "cloudflare.env", "service.prod.env", ".env"])
def test_prime_upload_rejects_sensitive_source_names_without_copy(tmp_path, monkeypatch, filename):
    from api import upload
    from api.identity import HermesIdentity
    monkeypatch.setattr(upload, "_attachment_root", lambda: tmp_path.resolve())
    monkeypatch.setattr("api.identity.resolve_request_identity", lambda handler: HermesIdentity("giorgio", "prime-a"))
    answers = []
    monkeypatch.setattr(upload, "j", lambda handler, body, status=200: answers.append((status, body)))
    upload.handle_prime_upload(_UploadHandler(filename, b"synthetic secret fixture"))
    assert answers[-1][0] == 400
    assert not list(tmp_path.rglob(filename))


def test_registered_artifact_digest_download_and_transcript_persist(tmp_path, monkeypatch):
    from api import prime_artifacts, prime_session_store
    monkeypatch.setattr(prime_artifacts, "_session_attachment_dir", lambda sid: tmp_path / sid)
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json", session_id="prime-a")
    monkeypatch.setattr("api.prime_session_store.get_prime_session_store", lambda sid: store)
    monkeypatch.setattr("api.routes._prime_workspace_from_settings", lambda settings: tmp_path / "workspace")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "result.txt"
    source.write_bytes(b"real output bytes")
    artifact = prime_artifacts.register_artifact("prime-a", str(source))
    row, body = prime_artifacts.artifact_bytes("prime-a", artifact["id"])
    assert body == b"real output bytes"
    assert row["sha256"] == hashlib.sha256(body).hexdigest()
    assert any(m.get("prime_artifacts", [{}])[0].get("id") == artifact["id"]
               for m in store.history()["messages"])
    assert json.loads((tmp_path / "prime-a" / "delivered" / "index.json").read_text())[0]["sha256"] == row["sha256"]
    with pytest.raises(FileNotFoundError):
        prime_artifacts.artifact_bytes("prime-b", artifact["id"])


def test_artifact_registry_rejects_external_missing_and_changed_files(tmp_path, monkeypatch):
    from api import prime_artifacts, prime_session_store
    monkeypatch.setattr(prime_artifacts, "_session_attachment_dir", lambda sid: tmp_path / sid)
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json", session_id="prime-a")
    monkeypatch.setattr("api.prime_session_store.get_prime_session_store", lambda sid: store)
    monkeypatch.setattr("api.routes._prime_workspace_from_settings", lambda settings: tmp_path / "workspace")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("no")
    with pytest.raises(ValueError, match="inside"):
        prime_artifacts.register_artifact("prime-a", str(outside))
    missing = workspace / "missing.txt"
    with pytest.raises(ValueError, match="missing"):
        prime_artifacts.register_artifact("prime-a", str(missing))
    target = workspace / "result.txt"
    target.write_text("before")
    artifact = prime_artifacts.register_artifact("prime-a", str(target))
    target.write_text("after")
    assert prime_artifacts.artifact_bytes("prime-a", artifact["id"])[1] == b"before"
    with pytest.raises(FileNotFoundError):
        prime_artifacts.artifact_bytes("prime-b", artifact["id"])
    link = workspace / "traversal.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is not available on this Windows account")
    with pytest.raises(ValueError, match="symlink"):
        prime_artifacts.register_artifact("prime-a", str(link))


def test_completed_delegation_report_authorizes_only_its_named_artifact(tmp_path, monkeypatch):
    from api import prime_artifacts, prime_session_store, prime_delegation
    monkeypatch.setattr(prime_artifacts, "_session_attachment_dir", lambda sid: tmp_path / sid)
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json", session_id="prime-a")
    monkeypatch.setattr("api.prime_session_store.get_prime_session_store", lambda sid: store)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("api.routes._prime_workspace_from_settings", lambda settings: workspace)
    delegated = tmp_path / "worker" / "deliverable.txt"
    delegated.parent.mkdir()
    delegated.write_text("delegated result")
    with pytest.raises(ValueError, match="inside"):
        prime_artifacts.register_artifact("prime-a", str(delegated))
    monkeypatch.setattr(prime_delegation, "get_background_tasks", lambda **kwargs: [
        {"status": "completata", "finished": 1, "output": f"File: {delegated}"}
    ])
    artifact = prime_artifacts.register_artifact("prime-a", str(delegated))
    assert prime_artifacts.artifact_bytes("prime-a", artifact["id"])[1] == b"delegated result"


def test_authenticated_artifact_route_sets_sandbox_and_exact_bytes(tmp_path, monkeypatch):
    from api import prime_artifacts, prime_session_store, routes
    monkeypatch.setattr(prime_artifacts, "_session_attachment_dir", lambda sid: tmp_path / sid)
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json", session_id="prime-a")
    monkeypatch.setattr("api.prime_session_store.get_prime_session_store", lambda sid: store)
    monkeypatch.setattr("api.routes._prime_workspace_from_settings", lambda settings: tmp_path / "workspace")
    monkeypatch.setattr(routes, "_request_prime_session_id", lambda handler: "prime-a")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "preview.html"
    source.write_bytes(b"<script>never-run()</script>")
    artifact = prime_artifacts.register_artifact("prime-a", str(source))

    class Handler:
        def __init__(self):
            self.headers_out = {}
            self.body = bytearray()
        def send_response(self, status): self.status = status
        def send_header(self, key, value): self.headers_out[key] = value
        def end_headers(self): pass
        @property
        def wfile(self): return self
        def write(self, data): self.body.extend(data)

    handler = Handler()
    from types import SimpleNamespace
    assert routes._handle_bridge_prime_artifact(handler, SimpleNamespace(path="/api/bridge/prime/artifacts/" + artifact["id"], query="inline=1")) is True
    assert handler.status == 200 and bytes(handler.body) == source.read_bytes()
    assert "sandbox" in handler.headers_out["Content-Security-Policy"]
    assert handler.headers_out["X-Content-Type-Options"] == "nosniff"


def test_browser_unsafe_prime_upload_requires_same_origin_csrf(monkeypatch):
    from api import routes, auth
    class Handler:
        headers = {"Origin": "https://prime.example", "Host": "prime.example", "X-CSRF-Token": "good"}
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "parse_cookie", lambda handler: "session")
    monkeypatch.setattr(auth, "verify_csrf_token", lambda cookie, token: token == "good")
    assert routes._check_csrf(Handler()) is True
    Handler.headers = {"Origin": "https://evil.example", "Host": "prime.example", "X-CSRF-Token": "good"}
    assert routes._check_csrf(Handler()) is False
    Handler.headers = {"Origin": "https://prime.example", "Host": "prime.example", "X-CSRF-Token": "bad"}
    assert routes._check_csrf(Handler()) is False


def test_site_zip_requires_readme_instructions(tmp_path, monkeypatch):
    import zipfile
    from api import prime_artifacts, prime_session_store
    monkeypatch.setattr(prime_artifacts, "_session_attachment_dir", lambda sid: tmp_path / sid)
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json", session_id="prime-a")
    monkeypatch.setattr("api.prime_session_store.get_prime_session_store", lambda sid: store)
    monkeypatch.setattr("api.routes._prime_workspace_from_settings", lambda settings: tmp_path / "workspace")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    archive = workspace / "site.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.html", "<script>bad()</script>")
    with pytest.raises(ValueError, match="README"):
        prime_artifacts.register_artifact("prime-a", str(archive))


def _artifact_fixture(tmp_path, monkeypatch):
    from api import prime_artifacts, prime_session_store
    monkeypatch.setattr(prime_artifacts, "_session_attachment_dir", lambda sid: tmp_path / sid)
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json", session_id="prime-a")
    monkeypatch.setattr("api.prime_session_store.get_prime_session_store", lambda sid: store)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr("api.routes._prime_workspace_from_settings", lambda settings: workspace)
    return prime_artifacts, store, workspace


def test_artifact_rejects_secret_source_even_with_innocent_alias_before_card(tmp_path, monkeypatch):
    prime_artifacts, store, workspace = _artifact_fixture(tmp_path, monkeypatch)
    source = workspace / "auth.json"
    source.write_text('{"synthetic":true}')
    with pytest.raises(ValueError, match="Credential"):
        prime_artifacts.register_artifact("prime-a", str(source), name="report.txt")
    assert not (tmp_path / "prime-a" / "delivered").exists()
    assert not store.history()["messages"]


@pytest.mark.parametrize("member", ["cloudflare.env", "nested/service.prod.env", "C:/private.txt",
                                      "\\\\server\\share\\private.txt", "../escape.txt", "bundle/.ssh/key"])
def test_zip_sensitive_names_paths_and_private_directories_rejected(tmp_path, monkeypatch, member):
    import zipfile
    prime_artifacts, store, workspace = _artifact_fixture(tmp_path, monkeypatch)
    archive = workspace / "site.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.md", "synthetic instructions")
        zf.writestr(member, "synthetic payload")
    with pytest.raises(ValueError, match="unsafe path or private file"):
        prime_artifacts.register_artifact("prime-a", str(archive))
    assert not (tmp_path / "prime-a" / "delivered").exists()
    assert not store.history()["messages"]


def test_safe_zip_and_document_remain_deliverable(tmp_path, monkeypatch):
    import zipfile
    prime_artifacts, _, workspace = _artifact_fixture(tmp_path, monkeypatch)
    doc = workspace / "report.txt"
    doc.write_text("synthetic document")
    doc_result = prime_artifacts.register_artifact("prime-a", str(doc))
    assert prime_artifacts.artifact_bytes("prime-a", doc_result["id"])[1] == b"synthetic document"
    archive = workspace / "site.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.md", "synthetic instructions")
        zf.writestr("index.html", "<p>synthetic</p>")
    zip_result = prime_artifacts.register_artifact("prime-a", str(archive))
    assert prime_artifacts.artifact_bytes("prime-a", zip_result["id"])[1] == archive.read_bytes()


def test_zip_symlink_member_is_rejected(tmp_path, monkeypatch):
    import stat
    import zipfile
    prime_artifacts, store, workspace = _artifact_fixture(tmp_path, monkeypatch)
    archive = workspace / "site.zip"
    link = zipfile.ZipInfo("linked.txt")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.md", "synthetic instructions")
        zf.writestr(link, "target.txt")
    with pytest.raises(ValueError, match="symbolic links"):
        prime_artifacts.register_artifact("prime-a", str(archive))
    assert not (tmp_path / "prime-a" / "delivered").exists()
    assert not store.history()["messages"]


def test_zip_source_cannot_bypass_member_filter_with_public_alias(tmp_path, monkeypatch):
    import zipfile
    prime_artifacts, store, workspace = _artifact_fixture(tmp_path, monkeypatch)
    archive = workspace / "site.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("README.md", "synthetic instructions")
        zf.writestr("cloudflare.env", "synthetic secret")
    with pytest.raises(ValueError, match="unsafe path or private file"):
        prime_artifacts.register_artifact("prime-a", str(archive), name="package.txt")
    assert not (tmp_path / "prime-a" / "delivered").exists()
    assert not store.history()["messages"]


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_document_upload_and_artifact_card_responsive_harness(browser, width, tmp_path):
    """Exercise real composer functions in an isolated fixture, no server.py."""
    page = browser.new_page(viewport={"width": width, "height": 900})
    try:
        from tests.test_prime_image_upload import ROOT
        source = (ROOT / "static/command_bridge.js").read_text(encoding="utf-8")
        scene(page, source)
        page.evaluate("uploadAttachment(new File(['hello'], 'brief.txt', {type:'text/plain'}))")
        assert page.evaluate("uploads[0].opts.body.get('file').name") == "brief.txt"
        page.evaluate("uploads[0].resolve({ok:true,status:200,json:()=>Promise.resolve({path:'/isolated/brief.txt',filename:'brief.txt',mime:'text/plain',size:5})})")
        page.wait_for_function("pendingAttachments[0].state==='ready'")
        evidence = __import__("os").getenv("HERMES_CAPTURE_FILE_EXCHANGE_UI")
        if evidence:
            out = Path(evidence)
            out.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(out / f"before-file-exchange-{width}.png"), full_page=True)
        page.evaluate("submit()")
        page.wait_for_function("posts.length===1")
        assert page.evaluate("posts[0].attachments") == [{"name": "brief.txt", "path": "/isolated/brief.txt"}]
        start = source.index("  function renderPrimeArtifacts(")
        end = source.index("\n  /*", start)
        page.add_script_tag(content=source[start:end])
        page.evaluate("renderPrimeArtifacts([{id:'0123456789abcdef0123456789abcdef',name:'brief.txt',mime:'text/plain',size:5}])")
        assert "brief.txt" in page.locator('[data-prime-artifact]').inner_text()
        assert page.locator('[data-prime-artifact] a').first.inner_text() == "Scarica"
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if evidence:
            page.screenshot(path=str(out / f"file-exchange-{width}.png"), full_page=True)
    finally:
        page.close()
