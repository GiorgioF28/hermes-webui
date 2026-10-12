"""Actual composer/status renderer in Chromium; no provider or server."""
import os
from pathlib import Path
import subprocess

import pytest

from tests.test_prime_brief_dedup import browser  # noqa: F401
from tests.test_prime_image_upload import scene


ROOT = Path(__file__).resolve().parents[1]


def setup(page, source):
    scene(page, source)
    controls = source[source.index('  function createPrimeTurnUi('):source.index('  function streamPrimeResponse(')]
    page.add_script_tag(content=controls + """
    function renderRich(node,text){node.textContent=text}
    function updatePrimeLiveText(root,text){root.querySelector('.cb-bubble').textContent=text}
    function applyPrimeTimestamp(){}
    function streamPrimeResponse(response,handlers){window.turnHandlers=handlers;return new Promise(()=>{})}
    """)
    page.evaluate("delete window.turnHandlers;$('cbInput').value='D665 e D666: riportami i recap';submit()")
    page.wait_for_function('window.turnHandlers !== undefined')


@pytest.mark.parametrize('width', [1200, 650, 360])
def test_capacity_recovery_is_visible_and_settles_once(browser, width):
    page = browser.new_page(viewport={'width': width, 'height': 700})
    evidence = os.getenv('HERMES_CAPTURE_CAPACITY_UI')
    try:
        if evidence:
            baseline = subprocess.check_output(['git', 'show', '1047d312:static/command_bridge.js'], cwd=ROOT).decode('utf-8')
            setup(page, baseline)
            page.evaluate("turnHandlers.error({branch:'codex_exit',error:'Codex è uscito con errore durante il turno.'})")
            Path(evidence).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(evidence) / f'before-{width}.png'))
        setup(page, (ROOT / 'static/command_bridge.js').read_text(encoding='utf-8'))
        page.evaluate("turnHandlers.status({state:'capacity_fallback',model:'gpt-6-luna'})")
        bubble = page.locator('.cb-from-prime .cb-bubble')
        assert 'recupero con Codex Luna' in bubble.inner_text()
        assert page.evaluate('posts.length') == 1
        assert page.locator('.cb-sysnote').count() == 0
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        if evidence: page.screenshot(path=str(Path(evidence) / f'after-{width}.png'))
        page.evaluate("turnHandlers.token({text:'D665 verificata; D666 audit concluso.'});turnHandlers.done({reply:'D665 verificata; D666 audit concluso.'})")
        assert bubble.inner_text() == 'D665 verificata; D666 audit concluso.'
        assert page.locator('.cb-from-prime').count() == 1
        assert page.locator('.cb-prime-status').count() == 0
        assert page.evaluate('_cbOwnTurnCount') == 0
        if evidence: page.screenshot(path=str(Path(evidence) / f'delivered-{width}.png'))
    finally:
        page.close()
