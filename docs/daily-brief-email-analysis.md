# Daily Brief email analysis

The digest analysis follows the provider selected by Hermes Prime. Codex
(`codex`, `codex-cli`, or `openai-codex`) runs with Prime's chief model profile
through the existing Codex CLI login. The invocation is ephemeral, read-only,
and capped at 120 seconds. Anthropic remains supported through Hermes' existing
Anthropic credential store when Anthropic is selected.

Email analysis is sent in batches of at most 80 messages. Each provider reply
must be a complete JSON array with one valid result per email. A timeout,
invalid or partial reply, or provider error retains the deterministic rules
fallback and the source error; failed rows remain pending in the durable
archive for a later digest attempt. No provider credentials are added to the
repository.
