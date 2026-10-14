# CRM territoriale Sicilia — MVP personale

Questo modulo è una prima prova personale, autorizzata per l'identità Hermes di
Giorgio; un'altra identità Hermes riceve 403. Non è un CRM multiagente e non
implementa account, ruoli, tenant, assegnazioni, audit amministrativo, offline o policy di
retention. Non inserire dati reali finché titolarità, informativa, accessi e
conservazione non sono stati valutati.

## Componenti

- `static/sicilia_crm/index.html`, `app.js`, `app.css`: mappa 2D OpenStreetMap,
  lista/dettaglio, modulo manuale, acquisizione GPS una tantum e sessione AI.
- `api/sicilia_crm.py`: handler API, validazione, estrazione bozza e SQLite.
- Il database è `STATE_DIR/sicilia-crm.sqlite3`, fuori dal repository.
- I record iniziali sono vuoti. La demo è sintetica, esplicita e non viene
  inviata al server.
- La cartografia usa tile standard `https://tile.openstreetmap.org/{z}/{x}/{y}.png`
  con attribuzione visibile. I tile possono non caricarsi per rete, policy CSP o
  indisponibilità del servizio; lista e modulo restano utilizzabili. Seguire la
  policy d'uso tile OSM: https://operations.osmfoundation.org/policies/tiles/.
  Le richieste di tile rivelano a OpenStreetMap l'area visualizzata e l'indirizzo
  IP, ma non inviano record CRM. La pagina lo segnala; `referrerpolicy="origin"`
  evita di includere path o query personali nel Referer.

## Dispatch da integrare in `api/routes.py`

Il modulo resta isolato dal Command Bridge. Il dispatcher condiviso deve
instradare le richieste CRM alle funzioni dedicate e mantenere il middleware
esistente:

```python
# In handle_get(handler, parsed), prima del fallback finale `return False`:
if parsed.path.startswith("/api/sicilia-crm/"):
    from api.sicilia_crm import handle_get as handle_sicilia_crm_get
    return handle_sicilia_crm_get(handler, parsed)

# In handle_post(handler, parsed), solo dopo _authorize_request(handler),
# _check_csrf(handler) e la lettura JSON in `body`:
if parsed.path.startswith("/api/sicilia-crm/"):
    from api.sicilia_crm import handle_post as handle_sicilia_crm_post
    return handle_sicilia_crm_post(handler, parsed, body)
```

Entrambi gli handler restituiscono `False` per endpoint non propri. Non creare
una route pubblica, non saltare `check_auth`, autorizzazione Hermes o CSRF. Il
modulo verifica anche CSRF sulle POST come difesa in profondità. Endpoint:

- `GET /api/sicilia-crm/records` → `{records: [...], csrf_token}`
- `POST /api/sicilia-crm/records` → salva con idempotenza su
  `client_request_id` UUID e risponde `{record}` (201).
- `POST /api/sicilia-crm/drafts` → `{draft}`; non scrive record.

La UI è `/static/sicilia_crm/index.html`; una card Prime può aprire direttamente
questa pagina. Prime deve integrare route e card nel branch operativo. Il
presente worktree non modifica `api/routes.py`, card o bridge.

## Dati e confini

`author` deriva dalla sessione Hermes lato server, mai dal payload. `created_at`
è UTC lato server. Il record conserva separatamente coordinate del fix,
precisione, timestamp e altitudine facoltativa; `declared_latitude`/
`declared_longitude` conservano il pin corretto/dichiarato. La mappa usa il pin
dichiarato quando presente e altrimenti il fix. Altitudine non determina piano.
L'appuntamento accetta solo ISO 8601 con timezone esplicito, costruito per
Europe/Rome.

L'AI riusa l'adapter server-side `api.intake_agent._run_hermes_agent`, senza
toolset, chat Prime, transcript condiviso o scritture. Le risposte devono essere
JSON e passare una allowlist/validazione; output ambiguo o non conforme mostra
errore, lasciando disponibile l'inserimento manuale. Il provider configurato e
la rete non sono garantiti: la UI non simula l'AI.

## Verifica

```powershell
python -m pytest tests/test_sicilia_crm.py -q
node --check static/sicilia_crm/app.js
git diff --check
```

Test backend isolati usano SQLite temporaneo e callback AI finta; non contattano
provider o server Hermes. Verificare poi gli screenshot desktop 1200, narrow 650
e mobile 360. Un test browser non equivale a GPS fisico o provider reale.
