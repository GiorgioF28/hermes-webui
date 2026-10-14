# Integrated CRM flow evidence

These final views use the shipped HTML/CSS/JS with synthetic AI, storage and
GPS responses at 1200, 650 and 360 pixels. Cartography is deliberately unavailable
in this harness to verify that the editable form and save flow remain usable.
The original map screenshots in `../sicilia-crm-mvp/` show attributed OSM tiles.

`flow-ai-*` shows the **Conferma** action; `flow-bozza-*` shows its editable form
before any record write; `flow-salvato-*` shows the saved synthetic record.
The appointment remains 20 October 2026 at 17:00 in Europe/Rome.

Run `tests/test_sicilia_crm_ui.py` with
`HERMES_CAPTURE_CRM_FLOW=docs/ui-ux/sicilia-crm-flow` to regenerate. The separate
`test_sicilia_crm_http.py` verifies a real browser/dispatcher/temporary-SQLite
round trip. Neither test calls a real model or uses phone hardware.
