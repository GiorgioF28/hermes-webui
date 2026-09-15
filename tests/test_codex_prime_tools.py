import asyncio
import json
import threading
import urllib.request
import urllib.error
import pytest
from api import codex_prime as cp, prime_delegation as pd


@pytest.fixture
def runtime():
    runtime = cp.PrimeToolRuntime()
    yield runtime
    runtime.server.shutdown()
    runtime.server.server_close()
    runtime.loop.stop()


def rpc(runtime, token, method, params=None):
    req = urllib.request.Request(runtime.url, data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params or {}}).encode(),
                                 headers={'Authorization':'Bearer '+token,'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=10))


def test_shared_tools_and_revoked_capability(runtime, monkeypatch, tmp_path):
    monkeypatch.setattr(pd, '_load_bg_tasks', lambda *a: None)
    tools = cp.build_tools('hermes-prime', tmp_path)
    token = runtime.register(tools, threading.Event())
    names = {t['name'] for t in rpc(runtime,token,'tools/list')['result']['tools']}
    assert names == {'delega','task_done','ask_user','prime_history','team_status'}
    with pytest.raises(urllib.error.HTTPError) as error:
        rpc(runtime,'wrong','tools/list')
    assert error.value.code == 401
    runtime.revoke(token)
    with pytest.raises(urllib.error.HTTPError):
        rpc(runtime,token,'tools/list')


def test_real_delega_survives_turn_end(runtime, monkeypatch, tmp_path):
    monkeypatch.setattr(pd, '_load_bg_tasks', lambda *a: None)
    monkeypatch.setattr(pd, '_BG_TASKS', {})
    monkeypatch.setattr(pd, '_BG_REFS', set())
    monkeypatch.setattr(pd, '_persist_bg_task', lambda *a: None)
    monkeypatch.setattr(pd, '_model_for', lambda *a: (pd._CODEX_MODEL, 'Codex'))
    started, finish, completed = threading.Event(), threading.Event(), threading.Event()
    async def worker(*args):
        started.set()
        while not finish.is_set():
            await asyncio.sleep(.01)
        completed.set()
    monkeypatch.setattr(pd, '_run_and_store', worker)
    token = runtime.register(cp.build_tools('hermes-prime', tmp_path), threading.Event())
    response = rpc(runtime,token,'tools/call',{'name':'delega','arguments':{'task_type':'semplice','task':'test','agent':'memory-librarian'}})
    assert 'Delega avviata' in response['result']['content'][0]['text']
    assert started.wait(2)
    assert list(pd._BG_TASKS.values())[0]['session_id'] == 'hermes-prime'
    runtime.revoke(token)
    finish.set()
    assert completed.wait(2)


def test_schema_blocks_invalid_delegation(runtime, monkeypatch, tmp_path):
    monkeypatch.setattr(pd, '_load_bg_tasks', lambda *a: None)
    token = runtime.register(cp.build_tools('hermes-prime',tmp_path), threading.Event())
    response = rpc(runtime,token,'tools/call',{'name':'delega','arguments':{'task': 'missing task_type'}})
    assert 'error' in response


def test_prompt_uses_canonical_session_and_full_persona(monkeypatch, tmp_path):
    from api import routes, memory_retrieval, prime_session_store
    monkeypatch.setattr(routes,'DEFAULT_WORKSPACE',tmp_path)
    monkeypatch.setattr(routes,'_prime_system_prompt_for_user',lambda w,u:'FULL PRIME mcp__team__delega')
    monkeypatch.setattr(memory_retrieval,'build_prime_unlocked_memory_detail',lambda *a,**k:'MEMORY')
    monkeypatch.setattr(memory_retrieval,'build_prime_memory_context',lambda *a,**k:'MEMORY')
    class Store:
        def history(self): return {'messages':[{'role':'user','content':'previous request'}]}
    monkeypatch.setattr(prime_session_store,'get_prime_session_store',lambda sid: Store())
    prompt=cp.build_prompt('current request',tmp_path,session_id='hermes-prime',user='giorgio')
    assert 'FULL PRIME mcp__hermes_prime__delega' in prompt
    assert 'previous request' in prompt and 'MEMORY' in prompt
    assert prompt.endswith('current request')

@pytest.mark.skipif(__import__('os').environ.get('HERMES_CODEX_TOOL_SMOKE') != '1', reason='opt-in real Codex CLI smoke')
def test_real_codex_calls_hermes_mcp(runtime, monkeypatch, tmp_path):
    from claude_agent_sdk import tool
    calls=[]
    @tool('hermes_probe', 'Read-only connectivity probe. Call this to verify Hermes MCP.', {'type':'object','properties':{}})
    async def probe(args):
        calls.append('called')
        return {'content':[{'type':'text','text':'HERMES_MCP_CONNECTED'}]}
    monkeypatch.setattr(cp,'get_runtime',lambda:runtime)
    monkeypatch.setattr(cp,'build_tools',lambda *a:[probe])
    result=cp.run_prime('Use the hermes_prime MCP tool hermes_probe exactly once. Do not use shell, read files, or modify anything. Reply with the tool result only.', str(tmp_path), session_id='hermes-prime', cancel=threading.Event())
    assert calls == ['called']
    assert 'HERMES_MCP_CONNECTED' in result['reply']

@pytest.mark.asyncio
async def test_automatic_librarian_honors_codex_pin(monkeypatch, tmp_path):
    from api import agent_models
    monkeypatch.setattr(agent_models,'get_overrides',lambda:{pd._LIBRARIAN_AGENT_ID:'codex'})
    monkeypatch.setattr(pd,'_BG_TASKS',{})
    calls=[]
    async def codex(prompt,workspace,**kwargs):
        calls.append(kwargs['agent_id'])
        assert 'sync-hermes-brain' in prompt
        return 'memory updated'
    async def claude(*args,**kwargs):
        pytest.fail('Pinned Librarian must not use Claude')
    monkeypatch.setattr(pd,'_run_codex_worker_with_fallback',codex)
    monkeypatch.setattr(pd,'_run_worker',claude)
    await pd._run_librarian_serial('test','memoria','task','result',str(tmp_path))
    assert calls == [pd._LIBRARIAN_AGENT_ID]
