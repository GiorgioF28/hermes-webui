"""CRM launch stays in the project strip and leaves Prime chat untouched."""
import os
from pathlib import Path
import subprocess

import pytest

from tests.test_prime_brief_dedup import browser  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


def _scene(page, source, families):
    page.set_content('<base href="http://crm.test/"><style>body{margin:0;background:#0a0908}</style>'
                     '<main class="cb-root"><section class="cb-projects">'
                     '<h2 class="cb-sec-title">Progetti</h2><div id="cards" class="cb-grid"></div>'
                     '</section></main>')
    page.add_script_tag(content="""
        function $(id){return document.getElementById(id)}
        function el(tag, cls){var n=document.createElement(tag);n.className=cls||'';return n}
        function esc(s){var n=document.createElement('span');n.textContent=String(s||'');return n.innerHTML}
        function sparkDots(){return ''}
        function apiPost(){throw new Error('Unexpected project mutation')}
        function refresh(){}
        function api(){throw new Error('Unexpected API call')}
    """)
    start = source.index('  function injectStyles()')
    end = source.index('  /*', start)
    page.add_script_tag(content=source[start:end] + '\ninjectStyles();')
    start = source.index('  function loadProjOrder()')
    end = source.index('  function renderProjects(', start)
    page.add_script_tag(content=source[start:end])
    page.evaluate('families => renderFamiliesV3(document.getElementById("cards"),families)', families)


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_crm_launch_does_not_toggle_task_filter_or_move_card(browser, width):
    families = [
        dict(id='hermes', name='Hermes', priority=2),
        dict(id='visionbuilts', name='VisionBuilts', priority=2),
        dict(id='sicilia-crm', name='CRM Sicilia', priority=2,
             app_path='/static/sicilia_crm/index.html',
             primary_path='01-Projects/CRM territoriale Sicilia.md',
             children=[dict(id='crm-spec', name='Specifiche', path='01-Projects/CRM territoriale Sicilia.md')],
             tasks=[dict(text='Provare GPS', subproject_id='crm-spec')]),
    ]
    page = browser.new_page(viewport=dict(width=width, height=900))
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    source = (ROOT / 'static/command_bridge.js').read_text(encoding='utf-8')
    capture = os.getenv('HERMES_CAPTURE_CRM_CARD')
    if capture:
        destination = Path(capture)
        destination.mkdir(parents=True, exist_ok=True)
        baseline = subprocess.run(['git', 'show', 'dd32649c:static/command_bridge.js'],
                                  capture_output=True, encoding='utf-8', check=True).stdout
        _scene(page, baseline, families[:2])
        page.screenshot(path=str(destination / f'card-before-{width}.png'), full_page=True)
    _scene(page, source, families)
    card = page.locator('[data-family-id="sicilia-crm"]')
    link = card.get_by_role('link', name='Apri CRM Sicilia')
    assert link.get_attribute('href') == '/static/sicilia_crm/index.html'
    assert link.get_attribute('target') == '_blank'
    assert link.bounding_box()['height'] >= 44
    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    card.get_by_role('button', name='Specifiche', exact=True).click()
    assert card.get_by_role('button', name='Specifiche', exact=True).get_attribute('class').endswith('cb-active')
    if capture:
        page.screenshot(path=str(destination / f'card-after-{width}.png'), full_page=True)
    page.route('http://crm.test/static/sicilia_crm/index.html',
               lambda route: route.fulfill(body='<title>CRM launch target</title>'))
    with page.expect_popup() as opened:
        link.click()
    opened.value.close()
    assert card.get_by_role('button', name='Specifiche', exact=True).get_attribute('class').endswith('cb-active')
    assert not card.get_attribute('class').endswith('cb-dragging')
    assert not errors
    page.close()


def test_launcher_rejects_untrusted_path(browser):
    page = browser.new_page()
    source = (ROOT / 'static/command_bridge.js').read_text(encoding='utf-8')
    _scene(page, source, [])
    for path in ['https://other.test/app', '//other.test/app', '/static/../secrets.html', '/api/shutdown']:
        assert page.evaluate('f => projectAppHtml(f)', dict(name='Unsafe', app_path=path)) == ''
    page.close()
