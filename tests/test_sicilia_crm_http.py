"""Real browser -> shared dispatcher -> temporary SQLite, with a synthetic AI."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from urllib.parse import urlparse

from api import auth, routes, sicilia_crm
from tests import test_prime_brief_dedup as browser_support

browser = browser_support.browser


def test_browser_ai_draft_save_and_reload_through_real_dispatch(browser, monkeypatch, tmp_path):
    monkeypatch.setattr(sicilia_crm, 'DB_PATH', tmp_path / 'crm.sqlite3')
    monkeypatch.setattr(auth, 'is_auth_enabled', lambda: False)
    monkeypatch.setattr(sicilia_crm, 'extract_draft', lambda text: {
        'name': 'Contatto HTTP sintetico', 'outcome': 'potenziale', 'clarification': '',
    })

    class TrialHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not routes.handle_get(self, urlparse(self.path)):
                self.send_error(404)

        def do_POST(self):
            if not routes.handle_post(self, urlparse(self.path)):
                self.send_error(404)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), TrialHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    page = browser.new_page(viewport={'width': 360, 'height': 900})
    page.route('https://tile.openstreetmap.org/**', lambda route: route.abort())
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    try:
        page.goto(f'http://127.0.0.1:{server.server_port}/static/sicilia_crm/index.html')
        page.wait_for_function('document.querySelector("#connection-state").textContent.includes("Dati caricati")')
        page.locator('#open-ai').click()
        page.locator('#transcript').fill('Contatto HTTP sintetico interessato')
        page.locator('#make-draft').click()
        page.wait_for_function('document.querySelector("#record-dialog").open')
        assert sicilia_crm.list_records() == []
        page.locator('[name=pin_latitude]').fill('37.5')
        page.locator('[name=pin_longitude]').fill('14.2')
        page.locator('#save-record').click()
        page.wait_for_function('!document.querySelector("#record-dialog").open')
        rows = sicilia_crm.list_records()
        assert len(rows) == 1 and rows[0]['author'] == 'giorgio'
        assert rows[0]['pin_latitude'] == 37.5
        page.reload()
        page.wait_for_function('document.querySelectorAll(".record-row").length === 1')
        page.locator('.record-row').click()
        assert 'Contatto HTTP sintetico' in page.locator('#record-detail').inner_text()
        assert not errors
    finally:
        page.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert not thread.is_alive()
