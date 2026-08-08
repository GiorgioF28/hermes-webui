# Profili contesto Hermes Prime

Il profilo predefinito resta `lean`, quindi un riavvio senza configurazione non
cambia il comportamento attuale.

## Attivazione unlocked

Nella stessa shell che avvierà la WebUI:

```powershell
$env:HERMES_PRIME_CONTEXT_PROFILE = "unlocked"
& "C:\Users\giorg\Documents\Hermes setup\scripts\serve-hermes-webui.cmd"
```

Il riavvio della porta 8788 resta una decisione di Giorgio. Il profilo viene
letto dal backend Python, quindi Ctrl+F5 da solo non basta.

Parametri opzionali:

- `HERMES_PRIME_MEMORY_TOP_K` — massimo note pertinenti, default `6`;
- `HERMES_PRIME_MEMORY_MAX_CHARS` — budget del dettaglio, default `12000`;
- `HERMES_PRIME_MEMORY_MIN_SCORE` — soglia lessicale, default `1.0`;
- `HERMES_PRIME_MEMORY_DIR` — directory contenente `MEMORY.md` e le note.

Per tornare al comportamento conservativo impostare
`HERMES_PRIME_CONTEXT_PROFILE=lean` e riavviare. Il vecchio
`HERMES_PRIME_USE_LEAN_PRESET` resta invariato e viene valutato solo dentro il
profilo `lean` per compatibilità.

## Layout cache

Il system prompt contiene solo persona completa, append di regole e indice
memoria. Brief progetti aggiornato, dettaglio memoria selezionato e messaggio
utente rimangono nella parte dinamica successiva gestita dal CLI Claude. I
breakpoint `cache_control` sono interni al CLI: il backend preserva il layout
cache-friendly introdotto da `f94600f1` e non prova a impostarli via SDK.
