"""Real browser/HTTP/MCP round trip with synthetic auth and provider output."""
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import pytest

from tests.test_prime_brief_dedup import browser  # noqa: F401
from tests.test_prime_image_upload import ROOT, scene
from tests.test_codex_prime_tools import rpc


@pytest.mark.parametrize("width", [1440, 768, 390])
def test_document_browser_codex_tool_download_and_reload(browser, tmp_path, monkeypatch, width):
    from api import auth, codex_prime, memory_retrieval
    from api import prime_delegation, prime_session_store, routes, upload

    inbox = tmp_path / "inbox"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = prime_session_store.PrimeSessionStore(tmp_path / "prime.json")
    monkeypatch.setenv("HERMES_WEBUI_ATTACHMENT_DIR", str(inbox))
    monkeypatch.setattr(prime_session_store, "_STORE", store)
    monkeypatch.setattr(routes, "DEFAULT_WORKSPACE", workspace)
    monkeypatch.setattr(routes, "_prime_workspace_from_settings", lambda settings: workspace)
    monkeypatch.setattr(routes, "_resolve_prime_model_state", lambda settings: {})
    monkeypatch.setattr(routes, "_prime_system_prompt_for_user", lambda *a: "Synthetic Prime persona")
    monkeypatch.setattr(memory_retrieval, "build_prime_unlocked_memory_detail", lambda *a, **k: "")
    monkeypatch.setattr(memory_retrieval, "build_prime_memory_context", lambda *a, **k: "")
    monkeypatch.setattr(prime_delegation, "get_background_tasks", lambda *a, **k: [])
    monkeypatch.setattr(routes, "_hermes_prime_reply", lambda *a, model_state=None, **k: routes._hermes_prime_reply_codex(*a, **k))
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "verify_session", lambda cookie: cookie == "synthetic-session")
    monkeypatch.setattr(auth, "verify_csrf_token", lambda cookie, token: cookie == "synthetic-session" and token == "test-token")
    runtime = codex_prime.PrimeToolRuntime()
    observed = {}

    def provider(prompt, cwd, *, session_id, cancel, on_token=None, **kwargs):
        observed["prompt"] = prompt
        source = inbox / session_id / "brief.txt"
        assert source.exists() and str(source) in prompt and "INPUT-SYNTHETIC" in prompt
        target = workspace / "risultato.txt"
        target.write_bytes(b"OUTPUT-SYNTHETIC\n")
        token = runtime.register(codex_prime.build_tools(session_id, workspace), cancel)
        try:
            response = rpc(runtime, token, "tools/call", {"name": "prime_deliver_file", "arguments": {"path": str(target)}})
            result = json.loads(response["result"]["content"][0]["text"])
            assert result["ok"] is True
            observed["artifact"] = result["artifact"]
        finally:
            runtime.revoke(token)
        if on_token:
            on_token("File pronto.")
        return {"reply": "File pronto.", "usage": {}}

    monkeypatch.setattr(codex_prime, "run_prime", provider)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path == "/":
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<html></html>")
            elif auth.check_auth(self, parsed):
                routes.handle_get(self, parsed)

        def do_POST(self):
            if not auth.check_auth(self, urlparse(self.path)):
                return
            if not routes._check_csrf(self):
                return routes.j(self, {"error": "CSRF"}, status=403)
            if self.path.endswith("/upload"):
                upload.handle_prime_upload(self)
            else:
                routes._handle_bridge_prime(self, json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    context = browser.new_context(viewport={"width": width, "height": 900})
    page = context.new_page()
    origin = f"http://127.0.0.1:{server.server_port}"
    source = (ROOT / "static/command_bridge.js").read_text(encoding="utf-8")
    render = source[source.index("  function renderPrimeArtifacts("):source.index("  // Un solo messaggio", source.index("  function renderPrimeArtifacts("))]

    def mount():
        page.goto(origin)
        scene(page, source)
        page.evaluate("origin=>{document.querySelector('base').href=origin+'/';window.fetch=window.nativeUploadFetch}", origin)
        page.add_script_tag(content=source[source.index("  function streamPrimeResponse("):source.index("  function _ensureCtx(")])
        page.add_script_tag(content=render)

    try:
        context.add_cookies([{"name": "hermes_session", "value": "synthetic-session", "url": origin}])
        mount()
        evidence = os.getenv("HERMES_CAPTURE_FILE_HTTP_UI")
        if evidence:
            Path(evidence).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(evidence) / f"http-before-{width}.png"))
        page.evaluate("uploadAttachment(new File(['INPUT-SYNTHETIC'], 'brief.txt', {type:'text/plain'}));submit()")
        page.wait_for_function("_cbOwnTurnCount===0 && posts.length===0 && pendingAttachments.length===0 && $('cbInput').value===''")
        history = context.request.get(origin + "/api/bridge/prime/history").json()
        messages = history["messages"]
        delivered = next(m for m in messages if m.get("prime_artifacts"))
        assert next(m for m in messages if m["role"] == "user")["attachments"][0]["name"] == "brief.txt"
        page.evaluate("m=>renderPrimeArtifacts(m.prime_artifacts)", delivered)
        assert page.locator("[data-prime-artifact]").count() == 1
        with page.expect_download() as download:
            page.get_by_text("Scarica", exact=True).click()
        assert Path(download.value.path()).read_bytes() == b"OUTPUT-SYNTHETIC\n"
        href = page.get_by_text("Apri", exact=True).get_attribute("href")
        preview = context.request.get(origin + "/" + href)
        assert preview.body() == b"OUTPUT-SYNTHETIC\n"
        assert "sandbox" in preview.headers["content-security-policy"]
        assert context.request.post(origin + "/api/bridge/prime/upload", headers={"Origin": "https://evil.invalid"}).status == 403
        anonymous = browser.new_context()
        try:
            assert anonymous.request.get(origin + "/api/bridge/prime/artifacts/" + observed["artifact"]["id"]).status == 401
        finally:
            anonymous.close()
        # Simulate process reconstruction, then cold browser reload and two polls.
        monkeypatch.setattr(prime_session_store, "_STORE", prime_session_store.PrimeSessionStore(store.path))
        mount()
        restored = context.request.get(origin + "/api/bridge/prime/history").json()
        for _ in range(2):
            for message in restored["messages"]:
                page.evaluate("m=>renderPrimeArtifacts(m.prime_artifacts)", message)
        assert page.locator("[data-prime-artifact]").count() == 1
        assert page.evaluate("document.documentElement.scrollWidth<=innerWidth")
        if evidence:
            page.screenshot(path=str(Path(evidence) / f"http-after-{width}.png"))
        for _ in range(51):
            prime_session_store._STORE.inject_assistant_message("Later message")
        assert not any(m.get("prime_artifacts") for m in context.request.get(origin + "/api/bridge/prime/history").json()["messages"])
        # Aging out of the view never invalidates an already delivered download.
        assert context.request.get(origin + "/api/bridge/prime/artifacts/" + observed["artifact"]["id"]).body() == b"OUTPUT-SYNTHETIC\n"
    finally:
        context.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
        runtime.server.shutdown()
        runtime.server.server_close()
        runtime.loop.stop()


def test_artifact_card_stays_in_its_message_and_leaves_with_history_window(browser):
    from tests.test_prime_timestamps import setup
    source = (ROOT / "static/command_bridge.js").read_text(encoding="utf-8")
    render = source[source.index("  function renderPrimeArtifacts("):source.index("  // Un solo messaggio", source.index("  function renderPrimeArtifacts("))]
    sizes = source[source.index("  function humanSize("):source.index("  function typeLabel(")]
    page = browser.new_page(viewport={"width": 390, "height": 900})
    try:
        setup(page, source)
        page.add_script_tag(content=render + sizes)
        page.evaluate("""var fileMessage={role:'assistant',content:'File pronto.',created_at:base+360,
          prime_artifacts:[{id:'0123456789abcdef0123456789abcdef',name:'risultato.txt',mime:'text/plain',size:17}]};
          renderPrimeHistoryMessage(fileMessage,14);renderPrimeHistoryMessage(fileMessage,14);
          renderPrimeHistoryMessage({role:'user',content:'Richiesta successiva',created_at:base+420},15);""")
        assert page.locator('[data-cb-msg-index="14"] [data-prime-artifact]').count() == 1
        assert page.locator('#cbLog > [data-prime-artifact]').count() == 0
        assert page.locator('#cbLog > [data-cb-msg-index]').last.get_attribute('data-cb-msg-index') == '15'
        page.evaluate("applyPrimeHistoryWindow({history_window:{start_index:15,visible_indexes:[15]}})")
        assert page.locator('[data-prime-artifact]').count() == 0
    finally:
        page.close()
