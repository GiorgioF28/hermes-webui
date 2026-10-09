# Daily Brief email analysis

Daily Brief analysis follows the provider selected by Hermes Prime. Codex uses
Prime's configured chief profile through the existing Codex CLI login;
Anthropic remains supported through Hermes' existing credential store when
selected. No provider credentials are added to the repository.

Analysis is batched at no more than 80 messages. Every input row must have one
valid result in the complete JSON response. A timeout, provider error, partial
or invalid reply does not publish an empty or rules-only recap: the endpoint
returns HTTP 503 and leaves the SQLite snapshot pending for retry. Deterministic
importance rules remain available for non-AI display paths but do not count as
a completed recap.

Email bodies remain private in SQLite while pending and are supplied to the
selected Prime provider for analysis. The public digest stores only sender,
subject, receive time, importance, summary, and why. After the full recap is
prepared, atomically published, and available to the existing Daily Brief API,
SQLite removes the bodies for only that snapshot and keeps identifier-only
receipts to prevent replay.
