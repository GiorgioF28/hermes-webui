# Hermes — Ciclo di memoria (potatura a 2 livelli + accesso memoria ad albero)

> Spec di design. Stato: **rivisto con Giorgio (decisioni prese, §12)**. Data: 2026-07-04.
> Autore: sessione Claude (brainstorming). Repo codice: `hermes-webui`.
> Memoria/dati: workspace `Hermes setup/` (Vault Obsidian, `graphify-out/`, `projects/`, `tasks/`).

---

## 1. Problema

La sessione persistente `hermes-prime` (client SDK) **cresce a ogni turno e non
viene mai potata**. Risultato: dopo poche task il contesto contiene l'80% di roba
di lavori già chiusi → token sprecati, crediti cloud bruciati (un solo comando su
Claude fable ha esaurito la finestra), risposte più lente. In parallelo, **Graphify
e Notion sono write-only**: il Librarian ci scrive ma nessun agente li legge, quindi
non aiutano il recupero. La memoria del Vault viene iniettata come estratto bounded
(~6k char), senza un modo mirato di recuperare *solo* ciò che serve.

## 2. Obiettivo

Un **ciclo di memoria** in cui:
- Prime lavora sempre con un **contesto leggero** (come chiedere al Librarian + due
  righe sui progetti in corso), mai la cronologia di task già chiuse.
- Quando una (sotto-)task finisce, l'essenziale è **salvato in memoria** in modo
  incrementale.
- Quando si cambia davvero argomento, la sessione viene **resettata** (potatura vera),
  senza perdere nulla (era già salvato).
- **Accesso alla memoria ad albero** (guidato dall'economia dei token): **Prime
  (Claude) accede solo al "tronco"** — logiche principali e mappa di come funzionano
  e sono gestite le componenti — tenuto leggero. Il **recupero profondo** lo delega al
  **Librarian (Codex)** via `ask_librarian` (es. per preparare bene una delega), così
  Prime **non spende token Claude** in ricerche dettagliate. Anche i **sotto-agenti
  (Codex)** leggono la memoria direttamente per informarsi. Nel tempo il Librarian
  **si auto-ottimizza** (meno ricerche/token, risposte più complete).

### Non-obiettivi (YAGNI)
- Non tocchiamo `tasks/active-context.md`: resta **solo** il meccanismo di ripresa
  dopo un **riavvio** (Ctrl+F5 / update WebUI). Non è la potatura.
- Niente riscrittura del protocollo memoria (`Hermes Memory Protocol.md`): lo si riusa.
- Niente potatura semantica fine dentro la sessione SDK (impossibile: la cronologia
  interna del client non è editabile). La potatura = reset + ripartenza pulita.
- Niente auto-summary costoso su Prime/Claude: riassunto e recupero li fa il
  **Librarian** (modello economico / Codex).
- **Nessun comando manuale di potatura/checkpoint** (deciso con Giorgio: no).

## 3. Il modello a due livelli (cuore del design)

Separiamo **"salva in memoria"** (frequente, non tocca il contesto vivo) da
**"resetta la sessione"** (raro, è la potatura dei token). Questo risolve il caso
dei sotto-task intrecciati: A finita mentre B e C continuano → salvo A ma **non**
resetto.

### Livello 1 — SALVA (frequente, incrementale, no reset)
**Trigger:**
- **Prime dichiara chiuso un (sotto-)task** via uno strumento leggero
  `task_done(nome, riassunto, stato)` — una tool-call, non un turno di ragionamento.
- **Commit su GitHub** rilevato nel workspace (qualcosa è cambiato per davvero).

**Azione:** si accoda un **pass Librarian** (async, già esistente:
`_enqueue_librarian_pass`) che scrive l'essenziale in memoria: Vault canonico →
Graphify → Notion, secondo il protocollo. **La sessione NON viene resettata.**

### Livello 2 — RESET (raro, potatura vera dei token)
**Trigger:**
- **Cambio argomento dell'utente**: apre una task genuinamente nuova ("ok ora
  occupiamoci di X"). Rilevato con euristica leggera e **conservativa** (§5).
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
2. **mappa del "tronco"**: le logiche principali e come sono gestite le componenti
   (alto livello) + come chiedere il **dettaglio profondo** al Librarian
   (`ask_librarian(bisogno)`). Non i contenuti profondi;
3. **due righe per progetto in corso**: da `projects/project-inventory.csv` /
   `tasks/today.md` (obiettivo + prossima azione, formato minimo del protocollo);
4. (invariato) `active-context.md` resta agganciato **solo** al percorso di
   ripresa-dopo-riavvio, non alla potatura.

Le task chiuse **non compaiono mai** qui: vivono in memoria, raggiungibili chiedendo
al Librarian.

## 5. Rilevatori dei trigger (dove vive la logica)

Nuovo modulo `api/memory_lifecycle.py` (puro/testabile dove possibile):

- `detect_commit(workspace) -> CommitEvent | None`: confronta l'HEAD dei repo
  rilevanti rispetto all'ultimo visto. Deterministico, idempotente.
- `is_new_topic(user_message, current_task) -> bool`: euristica leggera lato codice
  su frasi di apertura ("ora occupiamoci di", "passiamo a", "nuova cosa", "cambiamo")
  **combinata** con distanza dall'argomento corrente. **Conservativa: in dubbio NON
  resetta** (falso negativo < falso positivo).
- `task_done` tool (nel bridge server SDK di Prime): Prime lo chiama con
  `{nome, riassunto_1_riga, stato: chiuso|parziale}`.
- `checkpoint(level, payload, workspace)`: orchestratore. L1 → enqueue Librarian
  (SALVA). L2 → flush L1 pendenti + `_reset_prime_session` (RESET).

**Guard-rail:** un reset non parte mai mentre un turno di Prime è attivo
(`_PRIME_TURN_LOCK`); si applica al confine tra un turno e il successivo.

## 6. Accesso alla memoria ad albero (Obsidian + Graphify + Notion)

La memoria è un **albero**; l'accesso è **a livelli**, guidato dall'economia dei
token: **Prime gira su Claude (costoso) → accesso superficiale; gli agenti girano su
Codex (economico) → accesso profondo.**

**Livelli:**
- **Prime (Claude) — il tronco.** Accesso diretto ma **superficiale**: le logiche
  principali, la mappa di come funzionano e sono gestite le componenti. Sempre
  leggero, niente dettagli profondi (è ciò che sta nel contesto a regime, §4).
- **`ask_librarian(bisogno)` — recupero profondo su richiesta.** Quando Prime deve
  **preparare una delega** (es. definire le specifiche per il Programmatore su una
  modifica mirata) chiede al Librarian info dettagliate su codice/funzionamento. Il
  Librarian gira su **Codex** e fa la ricerca costosa al posto di Prime → **risparmia
  token Claude** e produce una delega migliore.
- **Sotto-agenti (Codex) — accesso diretto profondo.** Il Programmatore & co. leggono
  la memoria da soli per informarsi sul dettaglio che serve al loro task (hanno i tool
  di lettura). È giusto che si informino direttamente: sono su Codex.

**Substrato (per tutti i livelli):**
- **Obsidian (Vault)** = mappa **navigabile e canonica** (note MD + link + cartelle
  `00-Inbox … 04-Resources`, `01-Projects` + `projects/project-inventory.csv`,
  `tasks/today.md`). Precedenza dal protocollo.
- **Graphify** = **indice semantico sopra Obsidian** (`graphify-out/<ultima-data>/`:
  `.graphify_labels.json`, `.graphify_analysis.json`): argomento → note/entità e
  relazioni, per trovare il pezzo giusto senza aprire tutto.
- **Notion** = memoria pubblicata, letta via MCP `notion` (**in lettura**). Incluso in
  `memory_search`.

**Strumenti di lettura** (`memory_search`, `graphify_map`): disponibili al
**Librarian e ai sotto-agenti (Codex)**, **non a Prime**. Prime resta sul tronco +
`ask_librarian`, così Claude non paga le ricerche profonde. Ritornano **puntatori**
(percorso/URL + 1 riga), non dump. Nel tempo il Librarian raffina il recupero
(telemetria/cache su cosa serve a Prime) per meno token e risposte più complete.

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
- **Idempotenza**: un commit/`task_done` già processato non ri-scrive (dedup su
  hash/id, come `delegations.jsonl`).
- **Degrado morbido in lettura**: se Graphify o Notion non sono disponibili,
  `ask_librarian` risponde comunque col Vault (canonico) e segnala la fonte mancante.
- **Secrets**: redazione a monte (riusa `_redact_memory_log_text`).
- **Osservabilità**: ogni checkpoint logga `{livello, trigger, note toccate}` → card
  evento in UI (come le deleghe) così vedi *quando* e *perché* ha potato.

## 9. Testing

- `is_new_topic`: tabella di frasi (apertura nuova task vs prosecuzione sotto-task)
  → atteso reset / non-reset. (Il caso di Giorgio "ok A finita, ora B e C" ⇒ **non**
  reset.)
- `detect_commit`: nuovo commit ⇒ evento; nessun commit ⇒ None; commit già visto ⇒
  None (idempotente).
- `checkpoint`: L1 accoda Librarian e **non** resetta; L2 flush + reset; reset non
  parte se un turno è attivo (lock).
- `ask_librarian`/`memory_search`/`graphify_map`: ritornano puntatori curati (non
  dump), leggono l'artefatto Graphify più recente, **degradano** se Graphify/Notion
  assenti. Prime ha SOLO `ask_librarian` (+ tronco); i tool di lettura profonda sono
  del Librarian e dei sotto-agenti (Codex).
- Guardrail: se il Librarian fallisce, il reset è abortito.

## 10. Fasi di implementazione (ognuna testata e a sé)

1. **Contesto leggero** (§4): Prime smette di gonfiarsi; system prompt = persona +
   `ask_librarian` + basi progetti in corso. Guadagno token immediato, rischio basso.
   *Nessun reset ancora.*
2. **Recupero a livelli** (§6): `ask_librarian` (Prime→Librarian su Codex) +
   `memory_search`/`graphify_map` per Librarian **e** sotto-agenti, Notion in lettura,
   Graphify letto dal vault Obsidian.
3. **Livello 1 (SALVA)**: tool `task_done` + trigger commit → Librarian.
4. **Livello 2 (RESET)**: `is_new_topic` (conservativo) + grappolo-chiuso + guardrail
   flush/lock.
5. **Osservabilità + auto-ottimizzazione**: card evento potatura; il Librarian
   raffina il recupero (cache/telemetria su cosa serve a Prime) per meno token/ricerche.

Ordine pensato per dare valore subito (fasi 1-2 riducono già i token) e introdurre il
reset (fase 4, il pezzo più delicato) solo quando salvataggio e lettura sono solidi.

## 11. Componenti e confini (riassunto)

| Unità | Fa | Dipende da |
|---|---|---|
| `memory_lifecycle.py` | rileva trigger, orchestra checkpoint | git, prime session, Librarian |
| `task_done` tool (di Prime) | segnale semantico "task chiuso" | bridge server SDK |
| `ask_librarian` (di Prime) | recupero **profondo** su richiesta (spec delega) senza spendere token Claude | Librarian (Codex) |
| Librarian (esteso: read+write, su Codex) | scrive Vault→Graphify→Notion **e** recupera on-demand per Prime | protocollo memoria, tool sotto |
| `memory_search` / `graphify_map` (Librarian + sotto-agenti Codex) | pull mirato (puntatori); **non** a Prime | Vault fs (Obsidian), Graphify JSON, Notion MCP |
| `_reset_prime_session` (esistente) | potatura = reset SDK | lock turno |

## 12. Decisioni prese (review Giorgio, 2026-07-04)

- `is_new_topic` **conservativo** (in dubbio non resetta). ✅
- **Notion in lettura**: sì, incluso in `memory_search`. ✅
- **Graphify** = indice semantico **sopra Obsidian** (Obsidian = mappa navigabile
  canonica). ✅
- **Accesso ad albero** (per risparmiare i crediti Claude): Prime (Claude) accede
  solo al *tronco* (alto livello); il recupero **profondo** lo delega al **Librarian
  (Codex)** via `ask_librarian` (specie prima di delegare); i **sotto-agenti (Codex)**
  leggono la memoria direttamente. Librarian esteso a read+write, si auto-ottimizza. ✅
- **Nessun comando manuale** di potatura. ✅

### Ancora da definire in fase di piano
- Quale/i **database Notion** esporre a `memory_search` (mappatura DB → tipi di
  memoria: clienti, progetti, decisioni…).
