# Ciclo memoria — Fase 1: contesto leggero di Prime — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alleggerire il system prompt della sessione `hermes-prime` sostituendo il dump vault (~6k char) con un brief compatto dei soli progetti in corso + istruzione a delegare al Librarian per il dettaglio → risparmio token immediato, rischio minimo.

**Architecture:** Aggiungiamo un builder puro `_in_progress_projects_brief(workspace)` in `api/routes.py` che legge `projects/project-inventory.csv` (righe `status=active`) e produce 1-2 righe per progetto (nome + obiettivo + prossima azione). Poi `_hermes_prime_system_prompt` usa persona + brief + istruzione-Librarian, **rimuovendo** la chiamata a `_local_workspace_context` (che resta invariata e continua a servire gli altri prompt CLI).

**Tech Stack:** Python 3.11, stdlib `csv`, pytest. Nessuna nuova dipendenza.

## Global Constraints

- Repo: `hermes-webui`. File Python → **richiedono riavvio** del server per avere effetto (non toccare il runtime; segnalare il riavvio all'utente).
- Non toccare `_local_workspace_context` né `_obsidian_memory_context` (usati da altri prompt: righe ~14125/14292/14596). Cambiare **solo** `_hermes_prime_system_prompt`.
- `active-context.md` NON è coinvolto (resta il meccanismo di ripresa-dopo-riavvio).
- CSV con BOM → leggere con `encoding="utf-8-sig"`.
- Niente segreti nel contesto (già garantito: non leggiamo file di credenziali).
- Test nello stile di `tests/test_bridge_errors.py` (import `from api import routes`).

---

### Task 1: builder `_in_progress_projects_brief`

**Files:**
- Modify: `api/routes.py` (aggiunge la funzione vicino a `_local_workspace_context`)
- Test: `tests/test_prime_context.py` (nuovo)

**Interfaces:**
- Produces: `_in_progress_projects_brief(workspace, *, max_projects: int = 6, max_chars: int = 1600) -> str`
  - ritorna un brief multi-riga (`"Progetti in corso …\n- <name>: <goal> | prossima: <next>"`) considerando SOLO le righe `status == "active"`; `""` se il CSV manca o non ci sono progetti attivi.

- [ ] **Step 1: Write the failing test**

Crea `tests/test_prime_context.py`:

```python
"""Fase 1 ciclo-memoria: contesto leggero di Prime."""
from api import routes

_CSV = (
    '"project_id","name","repo_url","status","business_goal","next_action","ai_agent","last_reviewed"\n'
    '"p1","Alpha","","active","Vendere ebook","Inviare 5 DM","Codex","2026-06-01"\n'
    '"p2","Beta","","supporting","Interfaccia web","Testare microfono","Codex","2026-06-01"\n'
    '"p3","Gamma","","archived","Vecchio","Niente","Codex","2026-05-01"\n'
)


def _mk_ws(tmp_path):
    proj = tmp_path / "projects"
    proj.mkdir()
    (proj / "project-inventory.csv").write_text(_CSV, encoding="utf-8")
    return str(tmp_path)


def test_brief_includes_active_projects(tmp_path):
    brief = routes._in_progress_projects_brief(_mk_ws(tmp_path))
    assert "Alpha" in brief
    assert "Vendere ebook" in brief
    assert "Inviare 5 DM" in brief


def test_brief_excludes_non_active(tmp_path):
    brief = routes._in_progress_projects_brief(_mk_ws(tmp_path))
    assert "Beta" not in brief   # supporting -> escluso
    assert "Gamma" not in brief  # archived -> escluso


def test_brief_empty_when_no_csv(tmp_path):
    assert routes._in_progress_projects_brief(str(tmp_path)) == ""


def test_brief_is_bounded(tmp_path):
    brief = routes._in_progress_projects_brief(_mk_ws(tmp_path), max_chars=40)
    assert len(brief) <= 60  # 40 + suffisso troncamento
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_prime_context.py -q`
Expected: FAIL con `AttributeError: module 'api.routes' has no attribute '_in_progress_projects_brief'`

- [ ] **Step 3: Write minimal implementation**

In `api/routes.py`, subito **prima** di `def _local_workspace_context(workspace):`, aggiungi:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_prime_context.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add api/routes.py tests/test_prime_context.py
git commit -m "feat(memory): brief progetti in corso per contesto Prime (fase 1)"
```

---

### Task 2: system prompt di Prime leggero

**Files:**
- Modify: `api/routes.py` — funzione `_hermes_prime_system_prompt(workspace)`
- Test: `tests/test_prime_context.py` (aggiunge casi)

**Interfaces:**
- Consumes: `_in_progress_projects_brief` (Task 1), `_hermes_prime_persona_text()` (esistente).
- Produces: `_hermes_prime_system_prompt(workspace)` invariato nella firma/return
  (`{"type":"preset","preset":"claude_code","append": <str>}`), ma `append` NON contiene
  più il dump vault (`_local_workspace_context`).

- [ ] **Step 1: Write the failing test**

Aggiungi in `tests/test_prime_context.py`:

```python
def test_system_prompt_is_lean(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_hermes_prime_persona_text", lambda: "PERSONA_X")
    ws = _mk_ws(tmp_path)
    sp = routes._hermes_prime_system_prompt(ws)
    append = sp["append"]
    # persona + brief presenti
    assert "PERSONA_X" in append
    assert "Alpha" in append
    # vecchio dump vault RIMOSSO
    assert "Contesto vault verificato" not in append
    assert "Obsidian memory" not in append
    # istruzione a delegare al Librarian per il dettaglio profondo
    assert "Librarian" in append


def test_system_prompt_shape_unchanged(tmp_path):
    sp = routes._hermes_prime_system_prompt(_mk_ws(tmp_path))
    assert sp["type"] == "preset"
    assert sp["preset"] == "claude_code"
    assert isinstance(sp["append"], str)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_prime_context.py -q`
Expected: FAIL su `test_system_prompt_is_lean` (append contiene ancora "Contesto vault verificato").

- [ ] **Step 3: Write minimal implementation**

In `api/routes.py`, sostituisci il corpo di `_hermes_prime_system_prompt`. Corpo attuale:

```python
def _hermes_prime_system_prompt(workspace):
    """Chief-of-staff persona (prompts/hermes-prime.md) + read-only vault context."""
    persona = _hermes_prime_persona_text()
    local_context = _local_workspace_context(workspace)
    append = (
        persona
        + "\n\n--- Contesto vault verificato (sola lettura) ---\n" + local_context
        + "\n\nRispondi breve (2-4 frasi), in italiano, da capo di stato maggiore."
    )
    return {"type": "preset", "preset": "claude_code", "append": append}
```

Nuovo corpo (rimuove il dump, aggiunge brief + istruzione Librarian):

```python
def _hermes_prime_system_prompt(workspace):
    """Chief-of-staff persona (prompts/hermes-prime.md) + contesto LEGGERO.

    Fase 1 ciclo-memoria: niente più dump del vault nel prompt di Prime. Prime
    tiene solo persona + basi dei progetti in corso; per il dettaglio profondo
    (codice, memoria, come funzionano le componenti) DELEGA al Librarian, che gira
    su Codex e non consuma i crediti Claude di Prime.
    """
    persona = _hermes_prime_persona_text()
    brief = _in_progress_projects_brief(workspace)
    parts = [persona]
    if brief:
        parts.append("--- Progetti in corso ---\n" + brief)
    parts.append(
        "Per dettagli profondi (codice, memoria, funzionamento delle componenti) "
        "NON ricostruirli a mente: delega al Librarian (task_type 'memoria'/'ricerca') "
        "e usa la sua risposta. Rispondi breve (2-4 frasi), in italiano, da capo di "
        "stato maggiore."
    )
    append = "\n\n".join(parts)
    return {"type": "preset", "preset": "claude_code", "append": append}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_prime_context.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Verify nothing else references the removed behavior**

Run: `python -c "import ast; ast.parse(open('api/routes.py',encoding='utf-8').read()); print('syntax OK')"`
Run: `python -m pytest tests/test_command_bridge_prime_streaming.py -q`
Expected: syntax OK; gli stream test di Prime restano verdi (non dipendono dal dump vault).

- [ ] **Step 6: Commit**

```bash
git add api/routes.py tests/test_prime_context.py
git commit -m "feat(memory): system prompt Prime leggero, dettaglio via Librarian (fase 1)"
```

---

## Self-Review

**Spec coverage (Fase 1 dello spec §4/§10):**
- "persona + basi progetti in corso" → Task 1 (brief) + Task 2 (prompt). ✅
- "niente cronologia/dump" → Task 2 rimuove `_local_workspace_context` dal prompt di Prime. ✅
- "per il dettaglio chiedi al Librarian" → istruzione in Task 2 (usa la `delega` già esistente; il tool dedicato `ask_librarian` arriva in Fase 2). ✅
- Fasi 2-5 (recupero a livelli, SALVA, RESET, osservabilità) → **fuori da questo piano**, avranno i loro piani.

**Placeholder scan:** nessun TODO/TBD; ogni step ha codice reale. ✅

**Type consistency:** `_in_progress_projects_brief(workspace, *, max_projects, max_chars) -> str` usato coerentemente in Task 2; `_hermes_prime_system_prompt` mantiene firma e return. ✅

## Note di transizione

Questo piano **non** introduce il tool `ask_librarian` né i trigger di potatura: solo il contesto leggero (guadagno token subito, zero rischio sul flusso). La Fase 2 (recupero a livelli: `ask_librarian` + `memory_search`/`graphify_map` su Codex, Notion in lettura) sarà il piano successivo, e lì definiremo anche **quali DB Notion** esporre.
