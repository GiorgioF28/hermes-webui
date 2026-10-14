# CRM Sicilia project card

The dedicated Command Bridge family card launches the territorial CRM in a new
tab with **Apri app**. Project tasks remain filtered independently, and launching
the app does not submit a Prime message or alter the voice, planet or delegation
runtime. The tracked project identity is `sicilia-crm`; Instagram CRM work keeps
its existing VisionBuilts attribution.

The changed state layers are project configuration, overview payload and the
project-strip DOM. CRM writes use their own storage; the Prime transcript is
not a CRM datastore. Rollback removes the project registration and launch link.

Synthetic before/after evidence uses the actual project-card renderer and CSS
at widths 1200, 650 and 360 pixels. The launch link has a 44-pixel touch target,
preserves an active subproject filter, and excludes unsafe/external paths. The
link opens a substituted target in tests; these images do not certify the CRM
backend, phone GPS or AI provider.

Validation:

```text
python -m pytest tests/test_projects_overview.py tests/test_worklog.py tests/test_sicilia_crm_card.py -q
node --check static/command_bridge.js
```

Set `HERMES_CAPTURE_CRM_CARD=docs/ui-ux/sicilia-crm` to regenerate images. Tests
use isolated state and Chromium without launching the application server.
