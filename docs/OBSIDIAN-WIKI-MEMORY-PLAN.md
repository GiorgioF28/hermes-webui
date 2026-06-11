# Piano: secondo cervello Obsidian con obsidian-wiki (Ar9av) + memoria Hermes

> Obiettivo: trasformare documenti, codice e **storia degli agenti** in un vault
> Obsidian **organizzato, collegato e auto-aggiornante** ("LLM Wiki" di Karpathy),
> che sia anche la memoria che Hermes/Prime e i sotto-agenti leggono come contesto.

## 0. Cos'è (in breve)
`obsidian-wiki` = **tool CLI + skill per agenti AI** (non un plugin Obsidian). Ingerisce
fonti → estrae concetti/entità/relazioni → fonde nelle pagine wiki (no duplicati) →
collega con `[[wikilink]]` → traccia le fonti nel frontmatter. Fa **delta** via
`.manifest.json`. Supporta nativamente la storia di `~/.claude`, `~/.codex`, **`~/.hermes`**.
Stack Python, MIT.

## 1. Installazione (scelta: via skill Claude Code → usa il tuo abbonamento, niente API key)
```bash
npx skills add Ar9av/obsidian-wiki
```
Questo installa le skill nelle dir agente (~/.claude) e il comando `wiki-update`.
> Nota: aggiunge **solo skill** (markdown che l'agente legge a richiesta), non hook/MCP
> → basso rischio (≠ dai plugin con hook che impallavano la CLI).

Config (una volta): `~/.obsidian-wiki/config` o `.env` con:
- `VAULT_PATH = C:\Users\giorg\Documents\Hermes setup\obsidian-vault`
- l'LLM = Claude (via Claude Code, niente key) oppure una API key se preferisci.

## 2. Stato attuale del vault (da migliorare)
Cartelle: `00-Inbox`, `01-Projects` (8 progetti, 1 nota ciascuno), `02-Ideas`,
`03-Areas`, `04-Resources`, `05-Daily`, `05-Source-Mirror`, `06-Agents` (17),
`99-Templates`, `Home.md`. ~110 note. Pochi wikilink, pochi task `- [ ]`.

**Problemi:** progetti = singola nota (poca struttura), poco collegamento tra note,
nessuna pagina-concetto/entità, nessuna tassonomia.

## 3. Prima ingestione (cosa dare in pasto, in ordine)
1. **Vault esistente** → consolida/collega le note attuali, crea pagine-concetto.
2. **Storia agenti** `~/.hermes`, `~/.claude`, `~/.codex` → cattura *cosa è stato
   fatto* dalle sessioni (decisioni, fix, bug) dentro il secondo cervello.
3. **Codice/docs dei progetti** (i repo in `01-Projects` / `active-repos.csv`) →
   pagine per progetto con architettura, decisioni, TODO.
Comando tipo: `wiki-update --vault <vault> --source <path|~/.hermes|repo>`.
La skill istruisce Claude Code a ingerire + fondere + linkare.

## 4. Organizzazione risultante (cosa produce)
- Note con frontmatter `summary: / provenance: / sources:`.
- `[[wikilink]]` automatici tra pagine correlate.
- Struttura **per progetto + conoscenza globale**.
- `_meta/taxonomy.md` (tag/tassonomia), `_insights.md` (analisi grafo), export grafo
  (JSON/GraphML/HTML) → si sposa col **Vault Planet** del Command Bridge.

## 5. Aggiornamento continuo (delta)
- `wiki-update` periodico (anche schedulato via i cron di Hermes) → ingerisce solo le
  fonti nuove/cambiate (`.manifest.json`), così il secondo cervello resta vivo.
- Idea: hook a fine-sessione che ingerisce l'ultima sessione agente nel vault.

## 6. Integrazione con Hermes (perché conta)
- Il **bridge Claude/Codex** di Hermes legge già il vault come memoria
  (`_local_workspace_context` / `_obsidian_memory_context` in `api/routes.py`).
  → Un vault meglio organizzato = **contesto migliore** per Prime e i sotto-agenti.
- I **sotto-agenti** (Phase 6) prenderanno i loro MD di memoria da `06-Agents/` e dalle
  pagine-progetto: obsidian-wiki tiene quelle pagine ricche e collegate.
- Il **Vault Planet** (Command Bridge) renderizza il grafo del vault: più wikilink =
  pianeta più ricco e connesso.

## 7. Passi operativi (checklist)
- [ ] `npx skills add Ar9av/obsidian-wiki`
- [ ] config: VAULT_PATH = il vault Hermes
- [ ] ingestione 1: vault esistente
- [ ] ingestione 2: `~/.hermes` (storia agenti)
- [ ] ingestione 3: repo dei progetti attivi
- [ ] verifica: wikilink, `_meta/taxonomy.md`, `_insights.md`, e che il Vault Planet
      mostri più connessioni
- [ ] schedulare `wiki-update` (delta) come cron Hermes

> ⚠️ L'ingestione usa l'LLM (il tuo Claude via skill): parte token. Inizia con una
> cartella piccola per tarare, poi allarga.
