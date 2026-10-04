"""Offline browser coverage of the real Bridge scaffold, CSS and planet renderer.

All API/voice/submit hooks are stubbed; no server or user state is opened.
"""
import mimetypes
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def browser():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        instance = p.chromium.launch(
            headless=True, channel=os.getenv("HERMES_TEST_BROWSER_CHANNEL") or None)
        yield instance
        instance.close()


def open_bridge(browser, width=390, height=844, before=False):
    page = browser.new_page(viewport={"width": width, "height": height})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    def serve(route):
        from urllib.parse import urlparse
        relative = urlparse(route.request.url).path.lstrip("/")
        asset = (ROOT / relative).resolve()
        if relative.startswith("static/") and asset.is_relative_to(ROOT / "static") and asset.is_file():
            route.fulfill(body=asset.read_bytes(), content_type=mimetypes.guess_type(asset)[0] or "application/octet-stream")
        else:
            route.fulfill(body="{}", content_type="application/json")

    page.route("https://bridge.test/**", serve)
    page.goto("https://bridge.test/")
    page.set_content('<header id="testHeader">Command Bridge</header><main id="mainBridge"></main>')
    page.add_style_tag(content=(ROOT / "static/style.css").read_text(encoding="utf-8"))
    page.add_style_tag(content="body{margin:0}#testHeader{height:44px;padding:10px;box-sizing:border-box}#mainBridge{height:calc(100dvh - 44px)}")
    page.add_script_tag(url="https://bridge.test/static/command_star.js", type="module")
    page.wait_for_function("typeof cbInitStar === 'function'")
    source = (subprocess.check_output(["git", "show", "6cdead07:static/command_bridge.js"], cwd=ROOT).decode("utf-8")
              if before else (ROOT / "static/command_bridge.js").read_text(encoding="utf-8"))
    css = source[source.index("  function injectStyles()"):source.index("  /* ── DOM scaffold")]
    start = "  function wireChatLayout(" if not before else "  function build()"
    scaffold = source[source.index(start):source.index("  /* ── Hermes Prime + Voice")]
    page.add_script_tag(content="""
var BUILT = false, submitted = [];
function $(id) { return document.getElementById(id); }
function el(tag, cls) { var node = document.createElement(tag); node.className = cls; return node; }
function injectFonts() {}
function mountPrimeCore(host) { cbInitStar(host); }
function nearBottom(log) { return log.scrollHeight-log.scrollTop-log.clientHeight < 64; }
function toggleDailyBrief() {}
function refreshRepoStatus() {}
function addDailyAdhoc() {}
function toggleListen() {}
function uploadAttachment() {}
function onPrimePaste() {}
function toggleVoice() {}
function cancelPrimeTurn() {}
function wireBrainControls() {}
function refreshBrainState() {}
function onPrimeSubmit(ev) {
  ev.preventDefault(); submitted.push($('cbInput').value);
  $('cbInput').value = ''; __cbAutoGrow();
}
function primeSay(role, text) {
  var row = el('div', 'cb-msg cb-from-' + (role === 'user' ? 'user' : 'prime'));
  var bubble = el('div', 'cb-bubble'); bubble.textContent = text;
  row.appendChild(bubble); $('cbLog').appendChild(row); return row;
}
""" + css + scaffold + "build();")
    page.evaluate("""() => {
      $('cbLog').innerHTML = '';
      primeSay('user', 'Possiamo rendere la chat più comoda sul telefono?');
      primeSay('prime', 'La conversazione occupa tutto lo spazio disponibile. Puoi continuare a leggere mentre scrivi una bozza lunga, poi tornare a modificarla senza perdere il testo.');
      primeSay('user', 'Voglio poter vedere i messaggi e tornare alla bozza.');
      primeSay('prime', 'Tocca la conversazione per comprimere la bozza a una riga. Tocca la bozza per riaprirla. I comandi restano sotto il testo.');
    }""")
    page.wait_for_function("document.querySelector('#cbPetals canvas').width > 0")
    page.wait_for_timeout(250)
    assert errors == []
    return page


def capture(page, name):
    if os.getenv("HERMES_CAPTURE_BRIDGE_UI"):
        folder = ROOT / "docs/ui-ux/bridge-mobile-chat"
        folder.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(folder / (name + ".png")))


@pytest.mark.parametrize("width", [1200, 650, 390, 360])
def test_footer_and_mobile_background(browser, width):
    page = open_bridge(browser, width)
    try:
        assert page.locator("#cbForm #cbActions #cbSend").count() == 1
        assert not page.locator("#cbTodos").is_visible()
        log = page.locator("#cbLog").bounding_box()
        actions = page.locator("#cbActions").bounding_box()
        assert actions["y"] >= log["y"] + log["height"]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        if width <= 680:
            assert page.evaluate("getComputedStyle($('cbChat')).backdropFilter") == "none"
            assert page.evaluate("getComputedStyle($('cbChat')).borderTopWidth") == "0px"
            assert page.evaluate("$('cbPetals').inert")
            assert page.evaluate("getComputedStyle(document.querySelector('#cbPetals canvas')).pointerEvents") == "none"
            assert page.locator("#cbChat").bounding_box()["width"] == width
            assert float(page.locator(".cb-msg").first.evaluate("n => getComputedStyle(n).fontSize").replace("px", "")) == 13
        else:
            assert not page.evaluate("$('cbPetals').inert")
        capture(page, f"after-{width}")
    finally:
        page.close()


@pytest.mark.parametrize("width", [1200, 650, 390])
def test_before_evidence(browser, width):
    if not os.getenv("HERMES_CAPTURE_BRIDGE_UI"):
        pytest.skip("Before images are captured on demand")
    page = open_bridge(browser, width, before=True)
    try:
        capture(page, f"before-{width}")
    finally:
        page.close()


def test_long_draft_round_trip_and_keyboard_resize(browser):
    page = open_bridge(browser)
    draft = ("Questo testo lungo deve rimanere identico, anche con accenti e a capo.\n" * 100)
    try:
        page.locator("#cbInput").fill(draft)
        page.evaluate("$('cbInput').setSelectionRange(65, 71)")
        assert page.locator("#cbConversationPeek").is_visible()
        capture(page, "draft-expanded")
        for height in [450, 650, 844]:
            page.set_viewport_size({"width": 390, "height": height})
            page.wait_for_timeout(100)
            assert page.locator("#cbLog").bounding_box()["height"] >= 56
            assert page.locator("#cbSend").bounding_box()["y"] + 44 <= height
        page.locator("#cbConversationPeek").click()
        assert page.locator("#cbChat").get_attribute("data-draft") == "collapsed"
        assert page.locator("#cbInput").input_value() == draft
        assert page.locator("#cbInput").bounding_box()["height"] == 44
        capture(page, "draft-collapsed")
        page.locator("#cbInput").focus()
        assert page.locator("#cbChat").get_attribute("data-draft") == "expanded"
        assert page.evaluate("[$('cbInput').selectionStart, $('cbInput').selectionEnd]") == [65, 71]
        page.locator("#cbLog").click(position={"x": 5, "y": 5})
        assert page.locator("#cbChat").get_attribute("data-draft") == "collapsed"
        page.locator("#cbSend").click()
        assert page.evaluate("submitted") == [draft]
        assert not page.locator("#cbConversationPeek").is_visible()
        assert page.locator("#cbInput").input_value() == ""
    finally:
        page.close()


@pytest.mark.parametrize("native", [False, True])
def test_fullscreen_exit_preserves_draft_and_conversation(browser, native):
    page = open_bridge(browser)
    try:
        if not native:
            page.evaluate("() => { $('mainBridge').firstElementChild.requestFullscreen = () => Promise.reject(Error('unsupported')); }")
        page.locator("#cbInput").fill("Bozza conservata")
        count = page.locator(".cb-msg").count()
        page.locator("#cbFullscreen").click()
        page.wait_for_function("$('cbFullscreen').getAttribute('aria-pressed') === 'true'")
        assert page.locator("#cbChat").bounding_box()["height"] == 844
        capture(page, "fullscreen" + ("-native" if native else "-fallback"))
        page.locator("#cbFullscreen").click()
        page.wait_for_function("!document.fullscreenElement")
        assert page.locator("#cbFullscreen").get_attribute("aria-pressed") == "false"
        assert page.locator("#cbInput").input_value() == "Bozza conservata"
        assert page.locator(".cb-msg").count() == count
        page.locator("#cbFullscreen").click()
        page.wait_for_timeout(100)
        page.keyboard.press("Escape")
        page.wait_for_function("$('cbFullscreen').getAttribute('aria-pressed') === 'false'")
    finally:
        page.close()
