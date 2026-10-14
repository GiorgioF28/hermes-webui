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

## Card e API

La card **CRM Sicilia**, registrata in `api/command_bridge_projects.py`, apre
`/static/sicilia_crm/index.html` in una nuova scheda senza inviare messaggi Prime.
`api/routes.py` instrada GET e POST `/api/sicilia-crm/` alle funzioni dedicate
dopo i controlli esistenti di identità e, sulle POST, CSRF e lettura JSON.
I percorsi CRM sconosciuti restituiscono 404.

Entrambi gli handler restituiscono `False` per endpoint non propri. Non creare
una route pubblica, non saltare `check_auth`, autorizzazione Hermes o CSRF. Il
modulo verifica anche CSRF sulle POST come difesa in profondità. Endpoint:

- `GET /api/sicilia-crm/records` → `{records: [...], csrf_token}`
- `POST /api/sicilia-crm/records` → salva con idempotenza su
  `client_request_id` UUID e risponde `{record}` (201).
- `POST /api/sicilia-crm/drafts` → `{draft}`; non scrive record.

La card cambia soltanto registro progetti, payload overview e DOM delle card;
non cambia pianeta, voce, transcript o delega asincrona Prime. Le API scrivono
solo il database CRM dedicato. Per rimuovere la funzione, togliere card e dispatch;
il database non viene eliminato.

## Dati e confini

`author` deriva dalla sessione Hermes lato server, mai dal payload. `created_at`
è UTC lato server. Il record conserva separatamente coordinate del fix,
precisione, timestamp e altitudine facoltativa; `declared_latitude`/
`declared_longitude` conservano il pin corretto/dichiarato. La mappa usa il pin
dichiarato quando presente e altrimenti il fix. Altitudine non determina piano.
L'appuntamento accetta solo ISO 8601 con timezone esplicito, costruito per
Europe/Rome. La bozza AI conserva l'orario locale nella scheda; gli orari ambigui
o inesistenti al cambio dell'ora richiedono correzione prima del salvataggio.
Una risposta GPS, AI o di salvataggio tardiva non modifica una nuova scheda.
Il retry della stessa richiesta conserva l'UUID, evitando record duplicati.

L'AI riusa l'adapter server-side `api.intake_agent._run_hermes_agent`, senza
toolset, chat Prime, transcript condiviso o scritture CRM. Le risposte devono essere
JSON e passare una allowlist/validazione; output ambiguo o non conforme mostra
errore, lasciando disponibile l'inserimento manuale. Il provider configurato e
la rete non sono garantiti: la UI non simula l'AI.

## Verifica

```powershell
python -m pytest tests/test_sicilia_crm.py tests/test_sicilia_crm_ui.py tests/test_sicilia_crm_dispatch.py tests/test_sicilia_crm_http.py tests/test_sicilia_crm_card.py tests/test_projects_overview.py tests/test_worklog.py -q
node --check static/sicilia_crm/app.js
git diff --check
```

I test usano SQLite temporaneo e risposte AI sintetiche. Il test HTTP collega
browser, dispatcher e database reali con un server di prova effimero, chiuso a
fine test; non avvia Hermes. Le prove UI coprono 1200/650/360 pixel, errori,
permessi negati e risultati tardivi. Un test browser non equivale a GPS fisico
o provider reale. Dopo attivazione decisa dall'operatore, verificare sul telefono
HTTPS: posizione e precisione, voce se supportata, Conferma → scheda → Salva e
rilettura. Non dedurre il piano dall'altitudine; può rimanere sconosciuto.
