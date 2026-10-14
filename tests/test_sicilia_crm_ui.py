"""CRM form behavior with synthetic transport/GPS, without a live server."""
import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from tests import test_prime_brief_dedup as browser_support

browser = browser_support.browser

ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(os.getenv('HERMES_CRM_UI_ASSETS', str(ROOT / 'static/sicilia_crm')))


def _page(browser, width=360):
    page = browser.new_page(viewport={'width': width, 'height': 900})
    store, submissions, drafts = [], [], []
    mode = {'lose_ack': False, 'hold_draft': False, 'pending_drafts': [], 'hold_save': False, 'pending_saves': []}
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script('''
        window.__fixes = [];
        navigator.geolocation.getCurrentPosition = (ok, fail) => window.__fixes.push({ok,fail});
    ''')

    def route(request):
        path = urlparse(request.request.url).path
        if path == '/api/sicilia-crm/records':
            if request.request.method == 'GET':
                request.fulfill(json={'records': store, 'csrf_token': 'test-token'})
            else:
                payload = request.request.post_data_json
                submissions.append(payload)
                record = next((r for r in store if r['client_request_id'] == payload['client_request_id']), None)
                if record is None:
                    record = dict(payload, id='saved-' + str(len(store)), author='test', created_at='2026-10-14T12:00:00Z')
                    store.append(record)
                if mode['hold_save']:
                    mode['pending_saves'].append((request, record))
                    return
                if mode['lose_ack']:
                    mode['lose_ack'] = False
                    request.abort()
                else:
                    request.fulfill(json={'record': record}, status=201)
        elif path == '/api/sicilia-crm/drafts':
            drafts.append(request.request.post_data_json)
            if mode['hold_draft']:
                mode['pending_drafts'].append(request)
                return
            request.fulfill(json={'draft': {'name': 'Rossi demo', 'outcome': 'potenziale',
                                           'appointment_at': '2026-10-20T17:00:00+02:00'}})
        else:
            name = Path(path).name or 'index.html'
            asset = ASSETS / name
            request.fulfill(path=str(asset), content_type={'.html': 'text/html', '.js': 'text/javascript',
                                                          '.css': 'text/css'}[asset.suffix])

    page.route('https://crm.test/**', route)
    page.route('https://tile.openstreetmap.org/**', lambda request: request.abort())
    page.goto('https://crm.test/static/sicilia_crm/index.html')
    page.wait_for_function('document.querySelector("#connection-state").textContent.includes("Dati caricati")')
    return page, store, submissions, drafts, mode, errors


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_ai_confirmation_opens_editable_form_and_only_save_writes(browser, width):
    page, store, submissions, drafts, mode, errors = _page(browser, width)
    page.locator('#open-ai').click()
    page.locator('#transcript').fill('Rossi demo, appuntamento 20 ottobre 2026 alle 17.')
    capture = os.getenv('HERMES_CAPTURE_CRM_FLOW')
    if capture:
        destination = Path(capture)
        destination.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(destination / f'flow-ai-{width}.png'), full_page=True)
    page.locator('#make-draft').click()
    page.wait_for_function('document.querySelector("#record-dialog").open')
    assert page.locator('#record-dialog').evaluate('(dialog) => dialog.open')
    assert page.locator('[name=name]').input_value() == 'Rossi demo'
    assert page.locator('[name=appointment_at]').input_value() == '2026-10-20T17:00'
    assert len(drafts) == 1 and not submissions
    if capture:
        page.screenshot(path=str(destination / f'flow-bozza-{width}.png'), full_page=True)
    page.locator('#capture-location').click()
    page.evaluate('window.__fixes[0].ok({coords:{latitude:37.5,longitude:14.2,accuracy:12,altitude:null,altitudeAccuracy:null},timestamp:Date.now()})')
    assert 'altitudine non disponibile' in page.locator('#captured-fix').inner_text()
    page.locator('#save-record').click()
    page.wait_for_function('!document.querySelector("#record-dialog").open')
    assert len(submissions) == 1
    assert submissions[0]['latitude'] == 37.5 and submissions[0]['altitude_m'] is None
    assert submissions[0]['appointment_at'] == '2026-10-20T17:00:00+02:00'
    if capture:
        page.screenshot(path=str(destination / f'flow-salvato-{width}.png'), full_page=True)
    page.reload()
    page.wait_for_function('document.querySelectorAll(".record-row").length === 1')
    assert not errors
    page.close()


def test_closing_ai_discards_late_result_and_preserves_new_request(browser):
    page, store, submissions, drafts, mode, errors = _page(browser)
    mode['hold_draft'] = True
    page.locator('#open-ai').click()
    page.locator('#transcript').fill('Prima bozza demo')
    page.locator('#make-draft').click()
    page.wait_for_timeout(50)
    page.locator('.cancel-ai').click()
    page.locator('#open-ai').click()
    page.locator('#transcript').fill('Seconda bozza demo')
    page.locator('#make-draft').click()
    page.wait_for_timeout(50)
    assert len(mode['pending_drafts']) == 2
    mode['pending_drafts'][0].fulfill(json={'draft': {'name': 'Prima bozza demo'}})
    page.wait_for_timeout(50)
    assert not page.locator('#record-dialog').evaluate('(dialog) => dialog.open')
    assert page.locator('#make-draft').is_disabled()
    mode['pending_drafts'][1].fulfill(json={'draft': {'name': 'Seconda bozza demo'}})
    page.wait_for_function('document.querySelector("#record-dialog").open')
    assert page.locator('[name=name]').input_value() == 'Seconda bozza demo'
    assert not submissions and not errors
    page.close()


@pytest.mark.parametrize('local_time', ['2026-03-29T02:30', '2026-10-25T02:30'])
def test_nonexistent_or_ambiguous_rome_appointment_requires_correction(browser, local_time):
    page, store, submissions, drafts, mode, errors = _page(browser)
    page.locator('#new-record').click()
    page.locator('[name=name]').fill('Ora demo')
    page.locator('[name=appointment_at]').fill(local_time)
    page.locator('#save-record').click()
    assert 'cambio dell’ora' in page.locator('#form-error').inner_text()
    assert not submissions and not errors
    assert page.locator('#save-record').is_enabled()
    page.close()


def test_lost_save_ack_keeps_same_request_id_on_retry(browser):
    page, store, submissions, drafts, mode, errors = _page(browser)
    page.locator('#new-record').click()
    page.locator('[name=name]').fill('Retry demo')
    mode['lose_ack'] = True
    page.locator('#save-record').click()
    page.wait_for_function('!document.querySelector("#form-error").hidden')
    page.locator('#save-record').click()
    page.wait_for_function('!document.querySelector("#record-dialog").open')
    assert len(submissions) == 2
    assert submissions[0]['client_request_id'] == submissions[1]['client_request_id']
    assert len(store) == 1
    assert not errors
    page.close()


def test_late_save_ack_cannot_close_a_new_form(browser):
    page, store, submissions, drafts, mode, errors = _page(browser)
    mode['hold_save'] = True
    page.locator('#new-record').click()
    page.locator('[name=name]').fill('Prima scheda demo')
    page.locator('#save-record').click()
    page.wait_for_timeout(50)
    page.locator('.cancel-dialog').click()
    page.locator('#new-record').click()
    page.locator('[name=name]').fill('Seconda scheda demo')
    request, record = mode['pending_saves'][0]
    request.fulfill(json={'record': record}, status=201)
    page.wait_for_timeout(50)
    assert page.locator('#record-dialog').evaluate('(dialog) => dialog.open')
    assert page.locator('[name=name]').input_value() == 'Seconda scheda demo'
    assert page.locator('#save-record').is_enabled()
    assert not errors
    page.close()


def test_late_gps_fix_cannot_modify_a_different_form(browser):
    page, store, submissions, drafts, mode, errors = _page(browser)
    page.locator('#new-record').click()
    page.locator('#capture-location').click()
    page.locator('.cancel-dialog').click()
    page.locator('#new-record').click()
    page.locator('[name=name]').fill('Nessuna posizione demo')
    page.evaluate('window.__fixes[0].ok({coords:{latitude:37.5,longitude:14.2,accuracy:12,altitude:null,altitudeAccuracy:null},timestamp:Date.now()})')
    assert page.locator('#captured-fix').is_hidden()
    page.locator('#save-record').click()
    page.wait_for_function('!document.querySelector("#record-dialog").open')
    assert submissions[0]['latitude'] is None and submissions[0]['longitude'] is None
    page.locator('.record-row').click()
    assert 'Non acquisita' in page.locator('#record-detail').inner_text()
    assert not errors
    page.close()
