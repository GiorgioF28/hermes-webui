# Prime file exchange — report

## Esito

Implementato e committato sul worktree isolato `feat/prime-file-exchange-20261014`, partendo dal baseline `dd32649c0d8f041e451bdeaa610bf856cee8c232`.

Commit applicativo: `4bb899ab9685c841795d8bcd4fd045204fe772c3` (`feat(prime): add secure file exchange`).

**Non integrato e non live.** Nessun server o endpoint live è stato avviato/contattato; nessun merge, push o riavvio.

## Contract routing

- **Input:** `POST /api/bridge/prime/upload` accetta immagini raster, PDF, TXT, MD, CSV, JSON, DOCX, XLSX e PPTX. Il server controlla estensione, limite dimensione e nomi di segreti; salva nell'inbox privata della sessione Prime. Il `POST /api/bridge/prime` verifica che ogni path esista davvero, non sia symlink e appartenga all'inbox dell'identità autenticata prima di assegnarlo al turno.
- **Contesto modello:** il PDF mantiene il riassunto/path esistente. TXT/MD/CSV/JSON entrano con estrazione UTF-8 limitata a 12.000 caratteri; i formati binari restano riferimenti leggibili con nome, MIME, byte e path. Il prompt richiede un tool adatto prima di descriverne il contenuto e tratta il testo estratto come dato non attendibile. Gli allegati sono passati solo nel turno che li riceve; lo storico conserva metadati/path, non il contenuto binario.
- **Output:** il tool condiviso `prime_deliver_file` è esposto al Prime Codex e al Prime Claude SDK. Accetta un path esatto nella workspace/inbox oppure citato nel risultato di una delega completata della stessa sessione. Controlla path, dimensione, symlink, nomi sensibili; gli ZIP devono essere validi, contenere README/istruzioni e non avere path traversal, symlink o nomi di file privati. Registra snapshot immutabile e SHA-256 nell'indice privato; scrive una card assistant persistente nel transcript.
- **Download/open:** `GET /api/bridge/prime/artifacts/{id}` accetta solo ID opaco UUID e usa l'identità/sessione già autenticata dalla WebUI. Non accetta path dal browser. Il download è `attachment`; l'open compatibile è `inline` `?inline=1` con `nosniff` e CSP `default-src 'none'; sandbox`. L'HTML statico non può eseguire script nel contesto privilegiato. PDF, immagini raster e testo hanno link Apri; gli altri formati hanno sempre Scarica.
- **Persistenza/DOM:** `delivered/index.json` e gli snapshot sono nello stato allegati privato della sessione. L'endpoint history espone solo metadati pubblici, e il DOM ricostruisce card deduplicate per ID dopo refresh. Il transcript, il registro, il contesto modello e la scena DOM restano livelli distinti.
- **CSRF/origini:** gli unsafe POST continuano a passare dal gate globale WebUI; i test verificano origine e token. GET download resta protetto dall'autenticazione cookie/sessione già applicata dal router.

## File modificati (path assoluti)

- `C:\Users\giorg\Documents\Hermes setup\hermes-webui\.worktrees\prime-file-exchange-20261014\api\upload.py`
- `C:\Users\giorg\Documents\Hermes setup\hermes-webui\.worktrees\prime-file-exchange-20261014\api\bridge_attachments.py`
- `C:\Users\giorg\Documents\Hermes setup\hermes-webui\.worktrees\prime-file-exchange-20261014\api\prime_artifacts.py`
- `C:\Users\giorg\Documents\Hermes setup\hermes-webui\.worktrees\prime-file-exchange-20261014\api\prime_delegation.py`
- `C:\Users\giorg\Documents\Hermes setup\hermes-webui\.worktrees\prime-file-exchange-20261014\api\routes.py`
- `C:\Users\giorg\Documents\Hermes setup\hermes-webui\.worktrees\prime-file-exchange-20261014\static\command_bridge.js`
- `C:\Users\giorg\Documents\Hermes setup\hermes-webui\.worktrees\prime-file-exchange-20261014\prompts\hermes-prime.md`
- `C:\Users\giorg\Documents\Hermes setup\hermes-webui\.worktrees\prime-file-exchange-20261014\CHANGELOG.md`
- `C:\Users\giorg\Documents\Hermes setup\hermes-webui\.worktrees\prime-file-exchange-20261014\tests\test_prime_file_exchange.py`
- `C:\Users\giorg\Documents\Hermes setup\hermes-webui\.worktrees\prime-file-exchange-20261014\tests\test_command_bridge_prime_streaming.py` (fixture immagine aggiornata: usa path reale nella inbox temporanea)
- `C:\Users\giorg\Documents\Hermes setup\hermes-webui\.worktrees\prime-file-exchange-20261014\docs\ui-ux\prime-file-exchange\evidence\` (sei screenshot fixture)

## Verifiche

- `pytest -q -rs tests/test_prime_file_exchange.py tests/test_prime_pdf_attachments.py tests/test_prime_image_upload.py tests/test_command_bridge_reload_sync.py tests/test_prime_session_store_buffer.py tests/test_prime_stop_progress.py tests/test_prime_stop_progress_ui.py tests/test_command_bridge_prime_streaming.py`: **111 passed, 4 skipped** (1 test symlink non disponibile con i privilegi Windows; 3 prove UI preesistenti marcate evidence-only). Inclusi upload documento/immagine, dimensione/formati, digest e snapshot bytes identici, file assente/modificato, sessione, path esterni/symlink, ZIP, auth/CSRF, card refresh, retry, Stop e streaming.
- `python -m py_compile api/upload.py api/bridge_attachments.py api/prime_artifacts.py api/prime_delegation.py api/codex_prime.py api/routes.py`: PASS.
- `node --check static/command_bridge.js`: PASS. `git diff --check`: PASS.
- `graphify update .`: AST aggiornato, 31.318 nodi / 53.733 archi / 2.005 community. Visualizzazione HTML saltata dal limite automatico oltre 5.000 nodi; nessuna estrazione LLM.
- **Browser UI (fixture isolata Playwright, Chromium):** prima/dopo a 1440, 768 e 390 px. La prova esegue i metodi reali del composer e del renderer card, ma usa mock per la risposta upload e per i metadati output. Nessun `server.py` e nessun backend live. Prova bytes/backend, route/auth e transcript è coperta separatamente dai test handler/store.
  - Prima: `evidence/before-file-exchange-1440.png`, `evidence/before-file-exchange-768.png`, `evidence/before-file-exchange-390.png`.
  - Dopo: `evidence/file-exchange-1440.png`, `evidence/file-exchange-768.png`, `evidence/file-exchange-390.png`.

## Limiti residui

- DOCX/XLSX/PPTX non vengono convertiti/visualizzati inline: sono allegati con riferimento e richiedono un lettore compatibile; non si dichiara di averne letto il contenuto automaticamente.
- Nessuna prova end-to-end contro browser e processo WebUI live. Il report non afferma che il codice sia attivo su 8788.
- I link di download aprono una copia immutabile nello stato WebUI; non pubblicano siti/app e non eseguono/installano file.

## Rollback

Sul solo branch di questo worktree: `git revert 4bb899ab9685c841795d8bcd4fd045204fe772c3`. Non applicare tale commit al branch live senza revisione Prime.

## Stato Git osservato

Dopo il commit applicativo, prima di aggiungere questo report:

```text
## feat/prime-file-exchange-20261014
```

Il report viene salvato con commit docs separato. Stato finale del worktree riportato nella ricevuta di consegna dopo quel commit.

## Recupero mirato D687 — filtro file sensibili (2026-10-14)

- **Patch:** commit `c25f41222b7d2005ca8fa87b65ab7b3998a3bff7` sul branch isolato `feat/prime-file-exchange-20261014` (base `e8e9523db394fac124a483a9bf22f7fa4a1f10c3`). Controlla il basename originale prima della copia upload, anche con alias innocuo in consegna, e riconosce `.env`, `*.env` e nomi con spazi/punti finali. La validazione ZIP applica lo stesso filtro a sorgente e alias, compresi membri e directory private; rifiuta percorsi assoluti POSIX/Windows/UNC, traversal e symlink prima di leggere i byte dell'artefatto.
- **Regressioni sintetiche:** alias di `auth.json`; `.env`/`*.env` upload e ZIP; directory `.ssh`; path ZIP Windows assoluto, UNC e traversal; symlink ZIP; alias innocuo di ZIP. Verificano rifiuto senza snapshot/registro/card/transcript. Documento e ZIP legittimi continuano a essere consegnabili.
- **Verifiche rieseguite:** suite README (`pytest -q -rs tests/test_prime_file_exchange.py tests/test_prime_pdf_attachments.py tests/test_prime_image_upload.py tests/test_command_bridge_reload_sync.py tests/test_prime_session_store_buffer.py tests/test_prime_stop_progress.py tests/test_prime_stop_progress_ui.py tests/test_command_bridge_prime_streaming.py`): **126 passed, 4 skipped** in 24.97 s. Skip: symlink filesystem non disponibile con privilegi Windows e 3 prove UI evidence-only preesistenti. `node --check static/command_bridge.js`, `python -m py_compile api/upload.py api/prime_artifacts.py`, `git diff --check`: PASS. Nessuna nuova prova UI né E2E live.
- **Graphify:** aggiornamento AST senza estrazione LLM; 31.335 nodi, 53.761 archi, 2.051 community. HTML non generato perché il grafo supera il limite automatico di 5.000 nodi.
- **File D687:** `api/upload.py`, `api/prime_artifacts.py`, `tests/test_prime_file_exchange.py`; questo README aggiorna il report.
- **Stato:** **NON INTEGRATO, NON LIVE**. Tutte le prove usano file sintetici, mock/fixture locali e test handler; non dimostrano comportamento sul server o sugli account reali. Dopo il commit del report lo stato Git atteso è branch dedicato pulito (`## feat/prime-file-exchange-20261014`); nessuna modifica alla working copy principale.
