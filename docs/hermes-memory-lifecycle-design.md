# Hermes — Ciclo di memoria (potatura a 2 livelli + lettura Graphify/Notion)

> Spec di design. Stato: **da rivedere con Giorgio**. Data: 2026-07-04.
> Autore: sessione Claude (brainstorming). Repo codice: `hermes-webui`.
> Memoria/dati: workspace `Hermes setup/` (Vault, `graphify-out/`, `projects/`, `tasks/`).

---

## 1. Problema

La sessione persistente `hermes-prime` (client SDK) **cresce a ogni turno e non
viene mai potata**. Risultato: dopo poche task il contesto contiene l'80% di roba
di lavori già chiusi → token sprecati, crediti cloud bruciati (un solo comando su
Claude fable ha esaurito la finestra), risposte più lente. In parallelo, **Graphify
e Notion sono write-only**: il Librarian ci scrive ma nessun agente li legge, quindi
non aiutano il recupero. La memoria del Vault viene iniettata come estratto bounded
(~6k char), ma senza una *mappa* di come esplorarla in modo mirato.

## 2. Obiettivo

Un **ciclo di memoria** in cui:
- Prime lavora sempre con un **contesto leggero** (mappa memoria + due righe sui
  progetti in corso), mai la cronologia di task già chiuse.
- Quando una (sotto-)task finisce, l'essenziale è **salvato in memoria** in modo
  incrementale.
- Quando si cambia davvero argomento, la sessione viene **resettata** (potatura vera),
  senza perdere nulla (era già salvato).
- Vault / Graphify / Notion diventano **leggibili on-demand** tramite una mappa e
  strumenti mirati, invece di dump.

### Non-obiettivi (YAGNI)
- Non tocchiamo `tasks/active-context.md`: resta **solo** il meccanismo di ripresa
  dopo un **riavvio** (Ctrl+F5 / update WebUI). Non è la potatura.
- Niente riscrittura del protocollo memoria (`Hermes Memory Protocol.md`): lo si
  riusa.
- Niente potatura semantica fine dentro la sessione SDK (impossibile: la cronologia
  interna del client non è editabile). La potatura = reset + ripartenza pulita.
- Niente auto-summary costoso su Prime/Claude: il riassunto lo fa il Librarian
  (modello economico / Codex).

## 3. Il modello a due livelli (cuore del design)

Separiamo **"salva in memoria"** (frequente, non tocca il contesto vivo) da
**"resetta la sessione"** (raro, è la potatura dei token). Questo risolve il caso
dei sotto-task intrecciati: A finita mentre B e C continuano → salvo A ma **non**
resetto.

### Livello 1 — SALVA (frequente, incrementale, no reset)
**Trigger:**
- **Prime dichiara chiuso un (sotto-)task** via uno strumento leggero
  `task_done(nome, riassunto)` — una tool-call, non un turno di ragionamento.
- **Commit su GitHub** rilevato nel workspace (qualcosa è cambiato per davvero).

**Azione:** si accoda un **pass Librarian** (async, già esistente:
`_enqueue_librarian_pass`) che scrive l'essenziale in memoria: Vault canonico →
Graphify → Notion, secondo il protocollo. **La sessione NON viene resettata.**

### Livello 2 — RESET (raro, potatura vera dei token)
**Trigger:**
- **Cambio argomento dell'utente**: apre una task genuinamente nuova ("ok ora
  occupiamoci di X"). Rilevato con euristica leggera (vedi §5).
- **Grappolo chiuso**: nessun (sotto-)task in sospeso dopo un `task_done` → il
  sistema chiede *"cosa facciamo adesso?"*; alla ripartenza la sessione è pulita.

**Azione:** `_reset_prime_session` (già esistente) chiude e ricrea il client SDK.
Prima del reset ci si assicura che il Livello 1 abbia persistito lo stato corrente
(flush). Dopo il reset, Prime riparte col **contesto a regime** (§4).

**Difesa a più livelli:** se Prime dimentica `task_done`, il commit (L1) e il
cambio-argomento (L2) fanno da rete. Nessun singolo punto di fallimento.

**Fuori scope come trigger:** "delega completata" da sola (troppo granulare).

## 4. Contesto di Prime a regime (leggero, sempre)

Il system prompt di Prime (`_hermes_prime_system_prompt`) smette di dipendere dalla
cronologia e si compone di:
1. **persona** (`prompts/hermes-prime.md`) — invariata;
2. **mappa memoria** (§6): *come* trovare le cose, non le cose;
3. **due righe per progetto in corso**: da `projects/project-inventory.csv` /
   `tasks/today.md` (obiettivo + prossima azione, formato minimo del protocollo);
4. (invariato) `active-context.md` resta agganciato **solo** al percorso di
   ripresa-dopo-riavvio, non alla potatura.

Le task chiuse **non compaiono mai** qui: vivono in memoria, raggiungibili con la
mappa.

## 5. Rilevatori dei trigger (dove vive la logica)

Nuovo modulo `api/memory_lifecycle.py` (puro/testabile dove possibile):

- `detect_commit(workspace) -> CommitEvent | None`: confronta l'HEAD dei repo
  rilevanti (o legge un hook) rispetto all'ultimo visto. Deterministico.
- `is_new_topic(user_message, current_task) -> bool`: euristica leggera lato codice
  su frasi di apertura ("ora occupiamoci di", "passiamo a", "nuova cosa",
  "cambiamo") **combinata** con distanza dall'argomento corrente. Conservativa:
  in dubbio, NON resetta (falso negativo < falso positivo).
- `task_done` tool (in `prime_delegation` / bridge server): Prime lo chiama con
  `{nome, riassunto_1_riga, stato: chiuso|parziale}`.
- `checkpoint(level, payload, workspace)`: orchestratore. L1 → enqueue Librarian.
  L2 → flush L1 pendenti + `_reset_prime_session`.

**Guard-rail:** un reset non parte mai mentre un turno di Prime è attivo
(`_PRIME_TURN_LOCK`); si applica al confine tra un turno e il successivo.

## 6. Lettura Graphify / Notion / Vault (la "mappa" + tool on-demand)

### La mappa memoria (iniettata, corta)
Un blocco breve nel system prompt che dice **dove** vive la memoria e **come**
interrogarla, senza contenuti:
- **Vault** (filesystem MD): cartelle `00-Inbox … 04-Resources`, `01-Projects`,
  `projects/project-inventory.csv`, `tasks/today.md`. Regola di precedenza dal
  protocollo. Leggi una nota per **percorso** quando serve.
- **Graphify** (grafo semantico): indice in `graphify-out/<ultima-data>/` —
  `.graphify_labels.json`, `.graphify_analysis.json`. Serve a mappare
  **argomento → file/entità** e a esplorare relazioni senza aprire tutto.
- **Notion** (memoria pubblicata): via MCP `notion` (già configurato per la
  scrittura del Librarian), abilitato **anche in lettura** (query per DB/topic).

### Tool di lettura on-demand (nuovi, mirati)
- `memory_search(query) -> hit[]`: interroga in ordine Vault → Graphify → Notion e
  ritorna **puntatori** (percorso/URL + 1 riga), non dump. Prime/agenti leggono il
  contenuto solo del hit che serve.
- `graphify_map(topic) -> {files, entità, relazioni}`: legge gli artefatti
  `graphify-out/` più recenti e ritorna la sotto-mappa del topic.

Così il recupero è **pull mirato**, non **push massivo**.

## 7. Cosa si salva in memoria (tassonomia)

Riusa il **formato minimo progetto** del protocollo (obiettivo, stato, blocchi,
prossima azione, asset, agente). In più, categorie che **devono** restare
(indicate da Giorgio):
- **Stato clienti** (nuovo lavoro / lavoro completato → cambia stato).
- **Aggiornamenti a Prime** (cosa è cambiato nel suo comportamento/config).
- **Comportamento agenti** (se devono agire diversamente dopo una modifica).
- **Aggiornamenti console** (nuova sezione: com'è fatta e come modificarla senza
  rompere il resto).
- **Task completate** (1 riga + puntatori, non la cronologia).

**Mai salvare** segreti (token, password, API key, OAuth, `auth.json`) — già nel
protocollo; il Librarian redige.

## 8. Error handling / guardrail

- **Mai potare a metà turno** (lock).
- **Flush prima del reset**: se ci sono `task_done`/commit non ancora persistiti, si
  esegue il pass Librarian PRIMA del reset; se il Librarian fallisce, **si aborta il
  reset** e si segnala (meglio contesto gonfio che memoria persa).
- **Idempotenza**: un commit/`task_done` già processato non ri-scrive
  (dedup su hash/id, come `delegations.jsonl`).
- **Secrets**: redazione a monte (riusa `_redact_memory_log_text`).
- **Osservabilità**: ogni checkpoint logga `{livello, trigger, note toccate}` →
  card evento in UI (come le deleghe) così vedi *quando* e *perché* ha potato.

## 9. Testing

- `is_new_topic`: tabella di frasi (apertura nuova task vs prosecuzione sotto-task)
  → atteso reset / non-reset. (Il caso di Giorgio "ok A finita, ora B e C" ⇒ **non**
  reset.)
- `detect_commit`: nuovo commit ⇒ evento; nessun commit ⇒ None; commit già visto ⇒
  None (idempotente).
- `checkpoint`: L1 accoda Librarian e **non** resetta; L2 flush + reset; reset non
  parte se un turno è attivo (lock).
- `memory_search`/`graphify_map`: ritornano puntatori (non dump), leggono
  l'artefatto Graphify più recente, degradano se Graphify/Notion assenti.
- Guardrail: se il Librarian fallisce, il reset è abortito.

## 10. Fasi di implementazione (ognuna testata e a sé)

1. **Contesto leggero + mappa memoria** (§4, §6-map): Prime smette di gonfiarsi;
   guadagno token immediato, rischio minimo. *Nessun reset ancora.*
2. **Lettura on-demand** (§6-tool): `memory_search`, `graphify_map` + Notion read.
3. **Livello 1 (SALVA)**: tool `task_done` + trigger commit → Librarian.
4. **Livello 2 (RESET)**: `is_new_topic` + grappolo-chiuso + guardrail flush/lock.
5. **Osservabilità** (card evento potatura) + tuning euristiche.

Ordine pensato per dare valore subito (fase 1-2 riducono già i token) e introdurre
il reset (fase 4, il pezzo più delicato) solo quando salvataggio e lettura sono
solidi.

## 11. Componenti e confini (riassunto)

| Unità | Fa | Dipende da |
|---|---|---|
| `memory_lifecycle.py` | rileva trigger, orchestra checkpoint | git, prime session, Librarian |
| `task_done` tool | segnale semantico di Prime | bridge server SDK |
| mappa memoria (in system prompt) | *dove/come* leggere | Vault, `graphify-out/`, Notion |
| `memory_search` / `graphify_map` | pull mirato (puntatori) | Vault fs, Graphify JSON, Notion MCP |
| Librarian (esistente) | scrive Vault→Graphify→Notion | protocollo memoria |
| `_reset_prime_session` (esistente) | potatura = reset SDK | lock turno |

---

### Domande aperte (per la review)
- Soglia/tuning di `is_new_topic`: partiamo conservativi e aggiustiamo? (proposto: sì)
- Notion in lettura: quale/i database esporre a `memory_search`?
- Vogliamo anche un comando manuale ("pota adesso" / "checkpoint") come override?
