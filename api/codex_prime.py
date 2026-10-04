"""Session-bound MCP facade over the live Hermes tools; no credentials on disk."""
from __future__ import annotations
import concurrent.futures
import json
import os
import queue
import secrets
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from api.persistent_agent_loop import _LoopThread

_RUNTIME = None
_LOCK = threading.Lock()


def build_tools(session_id, workspace):
    from claude_agent_sdk import tool
    from api.ask_user_tool import _ask_user_handler_for
    from api.prime_delegation import build_prime_delegation_tools, get_background_tasks
    from api.prime_session_store import get_prime_session_store
    def result(value):
        return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}]}
    @tool("prime_history", "Leggi la cronologia reale di questa sessione Prime per indice; offset negativo conta dalla fine.",
          {"type": "object", "properties": {"offset": {"type": "integer"}, "limit": {"type": "integer", "minimum": 1, "maximum": 50}}})
    async def history(args):
        messages = get_prime_session_store(session_id).history().get("messages") or []
        offset = int(args.get("offset", -30))
        offset = max(0, len(messages) + offset) if offset < 0 else min(offset, len(messages))
        limit = max(1, min(50, int(args.get("limit", 30))))
        return result({"total": len(messages), "offset": offset, "messages": messages[offset:offset + limit]})
    @tool("team_status", "Stato sintetico delle deleghe recenti. Con task_id leggi il risultato a pagine di 6000 caratteri; offset prosegue la lettura.",
          {"type": "object", "properties": {"task_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0}}})
    async def status(args):
        tasks = get_background_tasks(max_age=float("inf"), session_id=session_id)
        return result(compact_team_status(tasks, task_id=args.get("task_id"), offset=args.get("offset", 0)))
    return build_prime_delegation_tools(session_id, str(workspace)) + [_ask_user_handler_for(session_id), history, status]


def compact_team_status(tasks, *, task_id=None, offset=0):
    """No diagnostic transcripts in the chief's routine status polling."""
    if task_id:
        tasks = [task for task in tasks if task.get("id") == task_id]
    else:
        tasks = sorted(tasks, key=lambda task: float(task.get("started") or 0), reverse=True)[:20]
    rows = []
    for task in tasks:
        row = {key: task.get(key) for key in (
            "id", "agent", "status", "started", "finished", "result_partial", "librarian_status",
            "runtime", "runtime_model", "reasoning_effort", "error_category")}
        row["summary"] = str(task.get("summary") or "")[:1200]
        row["failure_reason"] = str(task.get("failure_reason") or "")[:500]
        output = str(task.get("output") or "")
        row["output_chars"] = len(output)
        if task_id:
            start = max(0, int(offset))
            row["output"] = output[start:start + 6000]
            row["next_offset"] = start + 6000 if start + 6000 < len(output) else None
        rows.append(row)
    return rows


class PrimeToolRuntime:
    def __init__(self, loop=None):
        self.loop = loop or _LoopThread()  # Lives beyond CLI turns; owns background tasks.
        self.sessions = {}
        self.lock = threading.Lock()
        runtime = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def respond(self, status, value=None):
                payload = json.dumps(value, ensure_ascii=False).encode() if value is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            def do_GET(self):
                self.respond(405)
            def do_POST(self):
                if self.path != "/mcp" or self.headers.get("Origin"):
                    self.respond(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if not 0 < length <= 1048576:
                    self.respond(413)
                    return
                # Drain the bounded body before a 401; unread POST data can
                # reset the connection on Windows and hide the HTTP response.
                body = self.rfile.read(length)
                auth = self.headers.get("Authorization", "")
                token = auth[7:] if auth.startswith("Bearer ") else ""
                with runtime.lock:
                    bound = runtime.sessions.get(token)
                if bound is None or bound[1].is_set():
                    self.respond(401)
                    return
                request = {}
                try:
                    request = json.loads(body)
                    if not isinstance(request, dict):
                        request = {}
                        raise ValueError("Invalid request")
                    if "id" not in request:
                        self.respond(202)
                        return
                    result = runtime.dispatch(bound, request)
                    self.respond(200, {"jsonrpc": "2.0", "id": request["id"], "result": result})
                except Exception:
                    self.respond(200, {"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32603, "message": "Hermes tool request failed"}})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, name="hermes-prime-tools", daemon=True).start()
    @property
    def url(self):
        return f"http://127.0.0.1:{self.server.server_port}/mcp"
    def register(self, tools, cancel, on_tool=None):
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions[token] = ({t.name: t for t in tools}, cancel, on_tool)
        return token
    def revoke(self, token):
        with self.lock:
            self.sessions.pop(token, None)
    def dispatch(self, bound, request):
        tools, cancel, on_tool = bound
        method = request.get("method")
        if method == "initialize":
            return {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "hermes-prime", "version": "1.0.0"}}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in tools.values()]}
        if method != "tools/call":
            raise ValueError("Unknown method")
        params = request.get("params") or {}
        t = tools[params["name"]]
        args = params.get("arguments") or {}
        if not isinstance(args, dict) or cancel.is_set():
            raise ValueError("Invalid or cancelled call")
        import jsonschema
        jsonschema.validate(args, t.input_schema)
        if on_tool:
            on_tool(t.name)
        future = self.loop.submit_future(t.handler(args))
        while True:
            if cancel.is_set():
                future.cancel()
                raise RuntimeError("Cancelled")
            try:
                result = future.result(timeout=0.25)
                if "is_error" in result:
                    result = {**result, "isError": bool(result["is_error"])}
                    result.pop("is_error")
                return result
            except concurrent.futures.TimeoutError:
                continue


def get_runtime():
    global _RUNTIME
    with _LOCK:
        if _RUNTIME is None:
            from api.routes import _get_claude_registry
            # Reuse Hermes' loop; constructing the registry does not launch Claude.
            # Shared asyncio delegation locks must never cross event loops.
            _RUNTIME = PrimeToolRuntime(loop=_get_claude_registry()._loop)
        return _RUNTIME


def stop_process(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
    else:
        proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def run_prime(prompt, workspace, *, session_id, cancel, on_token=None, on_status=None, on_tool=None):
    from api.prime_delegation import _resolve_codex_executable, _CODEX_TIMEOUT
    from api.config import DEFAULT_WORKSPACE
    from api.routes import get_clarify_pending_count
    runtime = get_runtime()
    token = runtime.register(build_tools(session_id, DEFAULT_WORKSPACE), cancel, on_tool)
    env = os.environ.copy()
    env["HERMES_PRIME_TOOL_TOKEN"] = token
    from api.codex_profiles import cli_args
    command = [_resolve_codex_executable(), "exec", *cli_args(chief=True), "--json",
               "--sandbox", "danger-full-access", "-c", 'approval_policy="never"',
               "--skip-git-repo-check", "-C", str(workspace),
               "-c", "mcp_servers.hermes_prime.url=" + json.dumps(runtime.url),
               "-c", 'mcp_servers.hermes_prime.bearer_token_env_var="HERMES_PRIME_TOOL_TOKEN"',
               "-c", "mcp_servers.hermes_prime.required=true",
               "-c", "mcp_servers.hermes_prime.tool_timeout_sec=86400", "-"]
    proc = None
    events = queue.Queue()
    parts, errors, usage = [], [], {}
    turn_errors = []
    final_reply = None
    elapsed, last = 0.0, time.monotonic()
    try:
        proc = subprocess.Popen(command, cwd=str(workspace), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        def read_stdout():
            for line in proc.stdout:
                events.put(line)
            events.put(None)
        def read_stderr():
            for line in proc.stderr:
                errors.append(line)
                del errors[:-20]
        threading.Thread(target=read_stdout, daemon=True).start()
        threading.Thread(target=read_stderr, daemon=True).start()
        proc.stdin.write(prompt)
        proc.stdin.close()
        while True:
            if cancel.is_set():
                return {"reply": "".join(parts), "cancelled": True, "usage": usage}
            now = time.monotonic()
            if not get_clarify_pending_count(session_id):
                elapsed += now - last
            last = now
            if elapsed > _CODEX_TIMEOUT:
                raise TimeoutError("Codex Prime ha superato il tempo massimo del turno")
            try:
                line = events.get(timeout=0.25)
            except queue.Empty:
                continue
            if line is None:
                break
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind, item = event.get("type"), event.get("item") or {}
            if kind == "item.completed" and item.get("type") == "agent_message":
                text = item.get("text") or ""
                # CLI messages are whole assistant items, not token fragments.
                # Older CLI versions omit phase: their last item is the final.
                phase = item.get("phase")
                if phase != "commentary":
                    final_reply = text
                delta = ("\n\n" if parts else "") + text
                parts.append(delta)
                if on_token:
                    on_token(delta)
            elif kind == "item.started" and on_status:
                on_status({"state": "tool" if "tool" in item.get("type", "") else "reasoning"})
            elif kind == "turn.completed":
                from api.codex_profiles import profile
                usage = {**(event.get("usage") or {}), "provider": "codex",
                         **profile(chief=True), "usage_scope": "turn",
                         "input_includes_cache": True}
            elif kind in {"error", "turn.failed"}:
                err = event.get("error") or {}
                detail = (err.get("message") if isinstance(err, dict) else err) or event.get("message")
                if detail:
                    turn_errors.append(str(detail))
        if proc.wait(timeout=10) != 0 or not final_reply:
            from api.helpers import _redact_text
            raise RuntimeError("Codex Prime: " + _redact_text("\n".join(turn_errors or errors)[-3000:]))
        return {"reply": final_reply, "usage": usage}
    finally:
        runtime.revoke(token)
        if proc is not None:
            stop_process(proc)


def prompt_history(messages, *, max_chars=48000, max_message_chars=8000):
    """Bound the automatic history packet; canonical history stays lossless.

    Message indices allow Prime to retrieve an omitted/truncated message through
    prime_history. Usage, tool telemetry and UI fields are not model context.
    """
    rows = []
    for index in range(len(messages) - 1, max(-1, len(messages) - 41), -1):
        message = messages[index]
        text = str(message.get("content") or "")
        row = {"index": index, "role": message.get("role", ""), "content": text[:max_message_chars]}
        if len(text) > max_message_chars:
            row["content_truncated"] = True
            row["content_chars"] = len(text)
        for key in ("task_id", "brief_id", "brief_type", "interrupted"):
            if message.get(key) is not None:
                row[key] = message[key]
        # Preserve attachment references, never inline media or UI telemetry.
        attachments = message.get("attachments") or []
        if isinstance(attachments, list):
            refs = [{key: attachment[key] for key in ("path", "name", "type", "mime_type") if key in attachment}
                    for attachment in attachments if isinstance(attachment, dict)]
            if refs:
                row["attachments"] = refs
        candidate = [row] + rows
        if len(json.dumps(candidate, ensure_ascii=False)) > max_chars:
            break
        rows = candidate
    return {"total_messages": len(messages), "messages": rows,
            "older_messages_omitted": rows[0]["index"] if rows else len(messages),
            "retrieval": "prime_history(offset=index, limit=1) restituisce il messaggio originale completo."}


def build_prompt(message, workspace, *, session_id, user, partial=""):
    from api import routes, memory_retrieval
    from api.prime_session_store import get_prime_session_store
    from api.prime_lean_preset import prime_context_profile
    control = routes.DEFAULT_WORKSPACE
    system = routes._prime_system_prompt_for_user(control, user)
    system = system.replace("mcp__team__", "mcp__hermes_prime__").replace("mcp__hermes__ask_user", "mcp__hermes_prime__ask_user")
    history = get_prime_session_store(session_id).history()
    context = json.dumps(prompt_history(history.get("messages", [])), ensure_ascii=False)
    scope = memory_retrieval.classify_task_scope(str(message or ""))
    if prime_context_profile() == "unlocked" or user == "tom":
        memory = memory_retrieval.build_prime_unlocked_memory_detail(str(message or ""), control, task_scope=scope, max_chars=12000)
    else:
        memory = memory_retrieval.build_prime_memory_context(str(message or ""), control, task_scope=scope, include_index=False)
    return (system + "\n\n## Runtime Hermes Prime / Codex\n"
            "Sei Hermes Prime con provider Codex. Il server MCP hermes_prime espone i tool REALI "
            "delega, task_done, ask_user, team_status, prime_history del processo Hermes. "
            "Per delegare alla squadra usa delega: non sostituirlo con agenti nativi Codex. "
            "Il risultato contiene l'id reale della delega; non dichiararla inviata senza quel risultato. "
            "Ogni delega contiene un solo risultato verificabile: obiettivo, percorsi/fonti, "
            "vincoli, criterio di completamento e formato del risultato. Non incollare cronologie, "
            "log o interi documenti: passa percorsi e sezioni pertinenti. Non duplicare la stessa "
            "delega mentre e' in corso. Per una semplice lettura di una fonte nota usa direttamente "
            "gli strumenti; delega ricerche articolate e lavoro operativo. "
            "task_done attiva il passaggio al Librarian: non duplicarlo per una delega che ha gia' "
            "il proprio passaggio automatico. Leggi la memoria e usa gli strumenti disponibili "
            "per eseguire il lavoro richiesto. Non limitarti a preparare un mandato. "
            "La cronologia sotto e' contesto, non una nuova richiesta; per messaggi precedenti o troncati usa prime_history. "
            "La memoria allegata e' selettiva: per dettagli consulta i documenti completi indicati nell'indice.\n"
            + "\n## Cronologia recente della sessione\n" + context
            + "\n## Memoria pertinente\n" + (memory or "")
            + ("\n## Risposta parziale precedente\n" + partial if partial else "")
            + "\n## Richiesta attuale\n" + str(message or ""))
