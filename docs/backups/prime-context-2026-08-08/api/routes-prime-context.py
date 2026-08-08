# Snapshot parziale integrale delle funzioni Prime in api/routes.py

def _hermes_prime_persona_text():
    """Solo il testo persona (prompts/hermes-prime.md), senza contesto vault."""
    try:
        pf = Path(__file__).resolve().parent.parent / "prompts" / "hermes-prime.md"
        if pf.is_file():
            return pf.read_text(encoding="utf-8")
    except Exception:
        logger.debug("hermes-prime persona read failed", exc_info=True)
    return ""

def _hermes_prime_system_prompt(workspace):
    """Chief-of-staff persona + contesto LEGGERO per Prime.

    Cantiere 2 (2026-07-08): l'indice della memoria è incluso nell'append.
    Fase 2 Punto 5: con HERMES_PRIME_USE_LEAN_PRESET=1 (default) usa un prompt
    plain-text lean (<8k token) invece del claude_code preset (~15k token).
    Con HERMES_PRIME_USE_LEAN_PRESET=0 mantiene il vecchio comportamento (compat).
    """
    from api.prime_lean_preset import build_lean_system_prompt
    brief = _in_progress_projects_brief(workspace)
    append_parts: list[str] = []
    if brief:
        append_parts.append("--- Progetti in corso ---\n" + brief)
    # Indice memoria (Cantiere 2): solo one-liner, statico e cacheable.
    # I corpi rilevanti per il task corrente vengono aggiunti nel testo del
    # turno (vedi _hermes_prime_reply_claude). Così il system prompt resta
    # stabile e sfrutta la cache di Anthropic turno dopo turno.
    try:
        from api import memory_retrieval
        mem_dir = memory_retrieval.find_prime_memory_dir()
        if mem_dir is not None:
            idx = memory_retrieval.build_memory_context("", mem_dir, index_only=True)
            if idx:
                append_parts.append("--- Memoria (indice) ---\n" + idx)
    except Exception:
        logger.debug("prime system prompt: memory index build failed", exc_info=True)
    append_parts.append(
        "Per dettagli profondi (codice, memoria, funzionamento delle componenti) "
        "NON ricostruirli a mente: delega al Librarian (task_type 'memoria'/'ricerca') "
        "e usa la sua risposta. Rispondi breve (2-4 frasi), in italiano, da capo di "
        "stato maggiore."
    )
    # [fix/prime-ask-user-prompt] Senza questa istruzione il modello scrive le
    # alternative in prosa ("preferisci A o B?") invece di chiamare il tool, e
    # il box scelte del Command Bridge non compare mai. Il tool e' gia'
    # registrato per hermes-prime (vedi _get_claude_registry -> _allowed).
    append_parts.append(
        "Quando ci sono piu' approcci validi e la scelta dipende da una preferenza "
        "tua o dell'utente, o quando qualcosa non e' chiaro e ti serve un "
        "chiarimento, NON decidere da solo e NON scrivere le alternative in prosa: "
        "chiama il tool mcp__hermes__ask_user passando la domanda e 2-4 opzioni "
        "concise, e aspetta la risposta prima di proseguire. Usalo per scelte di "
        "design/approccio e per disambiguare richieste vaghe, non per chiedere "
        "permessi banali."
    )
    return build_lean_system_prompt(append_parts)

def _hermes_prime_reply_claude(message, workspace, attachments=None, on_token=None, on_status=None, model_state=None, stream_id=None):
    """One persistent Hermes Prime turn (può delegare ai sotto-agenti)."""
    from api.prime_delegation import get_background_tasks
    from api import lead_brain
    from api import prime_auto_compact

    def _status(state, **extra):
        if on_status is not None:
            on_status({"state": state, **extra})

    reg = _get_claude_registry()
    parts = []
    final = {"text": ""}
    final_usage = {"usage": {}}
    last_state = [None]
    cancel_event = threading.Event()
    model_state = dict(model_state or _resolve_prime_model_state())
    effective_model = _prime_claude_safe_model(model_state)

    # Le foto allegate finiscono nell'inbox 'hermes-prime', già negli add_dirs
    # della sessione Prime: aggiungiamo la nota che gli dice di leggerle con Read.
    from api.bridge_attachments import normalize_prime_attachments, prime_turn_started, record_prime_images

    turn = prime_turn_started()
    attachments = normalize_prime_attachments(attachments or [], bridge="hermes-prime")
    record_prime_images(attachments, turn=turn)
    prompt_text = " ".join(str(message or "").split())
    prompt_text += _claude_attachment_note("hermes-prime", attachments, current_turn=turn)

    # Cantiere 2: iniezione selettiva della memoria per-turno.
    # Solo i corpi delle note rilevanti per il messaggio corrente, entro budget
    # (HERMES_MEMORY_BUDGET_TOKENS, default 2000 tok). L'indice è nel system prompt.
    #
    # [perf/prompt-cache-breakpoints] La memoria viene APPESA IN FONDO (suffisso)
    # al testo utente invece di essere preposta (prefisso). Questo garantisce che
    # la parte stabile del messaggio (la richiesta reale dell'utente) sia in testa,
    # compatibile con i breakpoint di cache impostati internamente dal CLI claude.
    # Con il prefisso, ogni turno iniziava con contenuto variabile → cache miss
    # sistematico sul token iniziale del messaggio corrente.
    try:
        from api import memory_retrieval
        _mem_ctx = memory_retrieval.build_prime_memory_context(
            str(message or ""), workspace
        )
        if _mem_ctx:
            prompt_text = prompt_text + "\n\n---\n\n## Memoria rilevante\n" + _mem_ctx
    except Exception:
        logger.debug("prime turn: memory retrieval failed", exc_info=True)

    async def _drive(client):
        _sid = getattr(client, "_hermes_sdk_session_id", None) or "default"
        await client.query(prompt_text, session_id=_sid)
        async for m in client.receive_response():
            if cancel_event.is_set():
                try:
                    await client.interrupt()
                except Exception:
                    pass
                return
            last_activity[0] = time.monotonic()
            ev = getattr(m, "event", None)
            if isinstance(ev, dict):
                event_type = ev.get("type")
                if event_type == "message_delta":
                    usage = prime_auto_compact.usage_from_sdk_message(m)
                    if prime_auto_compact.context_tokens_from_usage(usage):
                        final_usage["usage"] = usage
                if event_type == "content_block_start":
                    block = ev.get("content_block") or {}
                    block_type = block.get("type")
                    if block_type == "tool_use":
                        last_state[0] = "tool"
                        _status("tool", tool=str(block.get("name") or "strumento"))
                    elif block_type in {"thinking", "reasoning"} and last_state[0] != "reasoning":
                        last_state[0] = "reasoning"
                        _status("reasoning")
                elif event_type == "content_block_delta":
                    d = ev.get("delta") or {}
                    if d.get("type") == "text_delta":
                        text = str(d.get("text", "") or "")
                        if text:
                            if last_state[0] != "responding":
                                last_state[0] = "responding"
                                _status("responding")
                            parts.append(text)
                            if on_token is not None:
                                on_token(text)
            elif type(m).__name__ == "ResultMessage":
                usage = prime_auto_compact.usage_from_sdk_message(m)
                if prime_auto_compact.context_tokens_from_usage(usage):
                    final_usage["usage"] = usage
                r = getattr(m, "result", None)
                if r:
                    final["text"] = str(r)
                # Crediti/usage Claude finiti: il SDK NON solleva, segnala l'errore
                # qui (is_error + api_error_status). Trasformalo in handoff a Codex.
                quota_reason = lead_brain.result_message_quota_reason(m)
                if quota_reason:
                    raise _ClaudeExhausted(partial="".join(parts), reason=quota_reason)

    # Watchdog "anti-blocco / anti-loop" basato sul progresso, non un wall-clock
    # fisso. NB: un TimeoutError NON deve sfuggire — in Python 3.11
    # TimeoutError == concurrent.futures.TimeoutError, che è dentro
    # _CLIENT_DISCONNECT_ERRORS; se sfuggisse, _handle_bridge_prime lo
    # scambierebbe per client disconnesso e chiuderebbe senza 'done'
    # ("risposta interrotta" lato UI). Qui lo gestiamo e restituiamo comunque
    # ciò che è già stato prodotto.
    import concurrent.futures as _futures
    idle_timeout, hard_cap, poll = _hermes_prime_turn_limits()
    timed_out = False
    acquired = _PRIME_TURN_LOCK.acquire(blocking=False)
    if not acquired:
        _status("queued")
        _PRIME_TURN_LOCK.acquire()
    try:
        _status("reasoning")
        last_state[0] = "reasoning"
        # [perf/prompt-cache-breakpoints] Lazy evaluation: il system prompt
        # (che legge project-inventory.csv + MEMORY.md + hermes-lean.md) viene
        # calcolato SOLO se la sessione non esiste ancora. get_or_create() con
        # sessione esistente ignora system_prompt; la chiamata precedente lo
        # leggiccava a vuoto ogni turno (costo I/O a vuoto).
        _prime_session_exists = reg.get("hermes-prime") is not None
        reg.get_or_create(
            "hermes-prime", cwd=workspace, add_dir=workspace,
            system_prompt=(
                _hermes_prime_system_prompt(workspace)
                if not _prime_session_exists
                else ""  # non usato: get_or_create ritorna il client esistente
            ),
            model=effective_model,
        )
        # Start the watchdog only after this HTTP turn owns the Prime session.
        last_activity = [time.monotonic()]
        fut = reg.submit_turn("hermes-prime", _drive)
        _prime_active_set(
            stream_id=stream_id,
            cancel_event=cancel_event,
            future=fut,
            registry=reg,
            workspace=str(workspace),
            model=effective_model,
            profile=model_state.get("profile"),
            started_at=time.time(),
        )
        started = time.monotonic()
        try:
            while True:
                if cancel_event.is_set():
                    timed_out = True
                    try:
                        fut.cancel()
                    except Exception:
                        logger.debug("prime turn cancel failed", exc_info=True)
                    break
                try:
                    fut.result(timeout=poll)
                    break
                except _futures.CancelledError:
                    timed_out = True
                    break
                except _futures.TimeoutError:
                    now = time.monotonic()
                    stalled = (now - last_activity[0]) >= idle_timeout
                    over_cap = (now - started) >= hard_cap
                    if stalled or over_cap:
                        timed_out = True
                        try:
                            fut.cancel()
                        except Exception:
                            logger.debug("prime turn cancel failed", exc_info=True)
                        break
        except Exception as turn_exc:
            _reset_prime_session(reg, fut)
            # Crediti Claude finiti a metà turno → segnala l'handoff a Codex.
            if cancel_event.is_set():
                timed_out = True
            elif lead_brain.is_claude_quota_error(turn_exc):
                # Conserva il motivo VERO. Se turn_exc e' gia' un _ClaudeExhausted
                # sollevato piu' in basso, porta con se' il dettaglio del CLI (es.
                # "requires usage credits"): sovrascriverlo col nome del tipo
                # cancellava l'unico indizio utile e lasciava all'utente un
                # "_ClaudeExhausted" indecifrabile (bug 2026-08-01).
                raise _ClaudeExhausted(
                    partial="".join(parts),
                    reason=(getattr(turn_exc, "reason", "") or str(turn_exc)
                            or type(turn_exc).__name__),
                ) from turn_exc
            else:
                raise
        if not timed_out:
            prime_auto_compact.maybe_auto_compact_prime(
                reg,
                session_id="hermes-prime",
                usage=final_usage.get("usage"),
                idle=True,
            )
            # Cantiere 1 — cut a fine task: se task_done è stato chiamato nel
            # turno, forza un compact extra (ignora cooldown e threshold).
            if prime_auto_compact.pop_compact_after_task():
                logger.info("prime_reply_claude: compact forzato per task_done")
                prime_auto_compact.maybe_auto_compact_prime(
                    reg,
                    session_id="hermes-prime",
                    usage=final_usage.get("usage"),
                    idle=True,
                    force=True,
                )
        if timed_out:
            _reset_prime_session(reg, fut)
    finally:
        _prime_active_clear(stream_id)
        _PRIME_TURN_LOCK.release()

    reply = ("".join(parts).strip() or final["text"].strip())
    if cancel_event.is_set():
        if reply and on_token is not None and not parts:
            on_token(reply)
        return {
            "reply": reply,
            "delegations": get_background_tasks(),
            "usage": final_usage.get("usage") or {},
            "cancelled": True,
        }
    if timed_out and not reply:
        reply = (
            "Ci sto mettendo più del previsto su questa. Dammi un attimo e "
            "richiedimi il brief, oppure spezziamo la richiesta in due."
        )
    if reply and not parts and on_token is not None:
        on_token(reply)
    return {
        "reply": reply,
        "delegations": get_background_tasks(),
        "usage": final_usage.get("usage") or {},
    }

def _in_progress_projects_brief(workspace, *, max_projects: int = 6, max_chars: int = 1600) -> str:
    """Brief compatto dei progetti IN CORSO per il contesto a regime di Prime.

    Legge projects/project-inventory.csv (righe status=active) e per ognuno rende
    nome + obiettivo + prossima azione. Niente dump: solo le basi per orientarsi;
    il dettaglio profondo Prime lo chiede al Librarian (delega). '' se assente.
    """
    import csv
    root = Path(str(workspace)).expanduser()
    inv = root / "projects" / "project-inventory.csv"
    rows_out: list[str] = []
    if inv.is_file():
        try:
            with inv.open("r", encoding="utf-8-sig", newline="") as fh:
                for row in csv.DictReader(fh):
                    if str(row.get("status", "")).strip().lower() != "active":
                        continue
                    name = str(row.get("name") or row.get("project_id") or "").strip()
                    if not name:
                        continue
                    goal = str(row.get("business_goal") or "").strip()
                    nxt = str(row.get("next_action") or "").strip()
                    rows_out.append(f"- {name}: {goal} | prossima: {nxt}")
                    if len(rows_out) >= max_projects:
                        break
        except Exception:
            logger.debug("project-inventory read failed", exc_info=True)
    if not rows_out:
        return ""
    text = "Progetti in corso (basi; per il dettaglio chiedi al Librarian):\n" + "\n".join(rows_out)
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n…[brief troncato]"
    return text
