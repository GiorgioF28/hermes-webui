"""Bake existing procedural Bridge textures once, outside page startup.
Run from repo root: python scripts/bake_bridge_textures.py
Requires Playwright Chromium, or BRIDGE_BAKE_BROWSER pointing to a Chromium browser.
Serves only static files on loopback.
"""
import base64, functools, http.server, json, os, threading
from pathlib import Path
from playwright.sync_api import sync_playwright
root = Path(__file__).resolve().parents[1]
class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args): pass
server = http.server.ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Quiet,directory=str(root)))
threading.Thread(target=server.serve_forever,daemon=True).start()
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path=os.environ.get('BRIDGE_BAKE_BROWSER') or None,headless=True)
    page=browser.new_page()
    page.goto(f'http://127.0.0.1:{server.server_port}/static/command_animation.js')
    page.evaluate("async()=>{window.THREE=await import('/static/vendor/three/three.module.js')}")
    page.add_script_tag(content=(root/'scripts/bridge_texture_source.js').read_text(encoding='utf-8'))
    out=root/'static/textures/bridge';out.mkdir(parents=True,exist_ok=True)
    for kind in ['solar','tech','social','green','exotic']:
        result=page.evaluate('''kind=>{const start=performance.now();const maps=kind==='solar'?solarTexture():planetMaps(kind);return {ms:performance.now()-start,images:Object.fromEntries(Object.entries(maps).map(([key,value])=>[key,value.image.toDataURL('image/png').split(',')[1]]))}}''',kind)
        for key,data in result['images'].items():
            (out/f'{kind}-{key}.png').write_bytes(base64.b64decode(data))
        print(json.dumps({'kind':kind,'generation_ms':round(result['ms'])}),flush=True)
    browser.close()
server.shutdown()
