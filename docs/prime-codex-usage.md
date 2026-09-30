# Prime Codex: model, usage and automatic context

Prime explicitly selects `gpt-6.1-sol` with reasoning effort `high`. Workers retain `gpt-6-luna/medium`. The model label reads that same profile rather than the unrelated desktop/global configuration.

Codex CLI `turn.completed.usage` contains totals for the turn, potentially across many model calls. `input_tokens` includes `cached_input_tokens`: do not add them together. The Bridge accepts these raw fields in saved messages, so historical cache statistics reappear without rewriting session data. Cache writes use `cache_write_input_tokens`; absent statistics remain unknown, whereas an explicit zero cache hit count is displayed as zero. Tokens do not directly measure remaining ChatGPT credits and API dollar rates are not a ChatGPT quota conversion.

The badge and tooltip show total input, output, cache hits, cache hit percentage and non-hit input. For 1,058,879 input and 981,504 cached tokens, non-hit input is 77,375, not another million. New usage records also retain provider, model, effort and turn scope.

## Automatic context

- Full persona and source index remain available.
- Recent history includes up to 40 messages within a 48,000-character packet, with at most 8,000 characters per message. It excludes usage counters and UI/tool telemetry. Truncation is explicit and original message indices are included.
- `prime_history(offset=index, limit=1)` retrieves the full original message from the canonical store. No stored messages are shortened or deleted.
- Codex automatic unlocked memory detail has a 12,000-character budget, even when the process has a larger general memory budget. The existing relevance selector and always-active priorities still apply. Complete notes remain reachable through the index. Other providers keep their previous settings.

State layers: explicit CLI profile, transient prompt and usage display only; canonical conversation content is unchanged. Model/profile changes require loading the new backend code. Validate the CLI model list and one minimal real request, run the focused tests and inspect badges at desktop/mobile widths before a separately authorized live restart.

Official references: https://developers.openai.com/api/docs/models/gpt-6.1-sol and https://developers.openai.com/api/docs/guides/prompt-caching.
