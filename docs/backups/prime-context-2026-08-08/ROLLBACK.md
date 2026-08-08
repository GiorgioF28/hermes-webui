# Rollback

La feature non modifica file di configurazione runtime. Sul branch della
feature, con working tree pulito, il rollback del codice è un solo comando:

```powershell
$env:HERMES_PRIME_CONTEXT_PROFILE = "lean"; git revert --no-edit feat/prime-context-unlocked
```

Il comando crea un commit di revert della punta feature e forza il profilo
conservativo nella shell corrente. Se la variabile è stata resa persistente nel
launcher o nel profilo utente, impostarla lì a `lean` prima del successivo
riavvio. Il server 8788 non viene riavviato dal rollback.
