# Backup Prime context — 2026-08-08

Snapshot effettuata prima della feature `feat/prime-context-unlocked`, dopo il
commit separato del residuo valido `ask_user`.

Contenuto:

- `api/prime_lean_preset.py`: modulo completo;
- `api/memory_retrieval.py`: modulo completo;
- `api/routes-prime-context.py`: funzioni complete responsabili di persona,
  system prompt, turno Prime e brief progetti;
- `prompts/hermes-prime.md` e `prompts/hermes-lean.md`: prompt completi.

Non sono stati copiati `.env`, `auth.json`, `config.yaml` o altri file runtime,
perché la feature non li modifica e possono contenere credenziali.

La snapshot è stata confrontata con `a8dbb38b` normalizzando solo CRLF/LF:

- `api/prime_lean_preset.py`: `429971b04e9c46369356a3d085c43a1788bc1360b706f441c24e6d954c51dab6`;
- `api/memory_retrieval.py`: `e1570068c621d0f29d7dd5cbd56447663e7d24c0fcbc83147697c54c85421f69`;
- `prompts/hermes-prime.md`: `ccf54b828d55bcd83bba12fc866c061d4be0840355707b8b9f2e04645bb03a67`;
- `prompts/hermes-lean.md`: `ddaa6252f2fbe3a7393a4d1ddaa0f194adcaecd830f2222c309c7e4ccd9f4f25`;
- funzioni Prime da `api/routes.py`: `d19c4864bc4c7a6e170e234966f8e131fc98187493cecb340eaeda2f8cac4852`.
