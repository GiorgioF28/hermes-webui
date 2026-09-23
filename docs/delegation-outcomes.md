# Delegation outcomes

Project: Hermes WebUI. Priority: P1.

A failed worker process does not prove that its files or deployments were lost.
The card shows the execution error, the agent result (when stdout contains one),
and a separate collapsed technical excerpt. Partial outcomes explicitly require
checking existing reports, files and commits before continuing work.

## State and replay contract

- `failure_reason` / `error.message`: at most 500 characters.
- `output` / `result.text`: the agent report, never the human Codex CLI transcript.
- `diagnostic_log` / `result.diagnostic_log`: at most 12,000 characters, retaining
  both the beginning and end, including the terminal error. This is an excerpt,
  not an archive of every tool result or a verified deliverable.
- Nonzero process exits preserve stdout independently from stderr. Empty stdout
  on exit zero remains empty and is handled by existing output validation.
- A terminal Codex usage-limit error takes precedence over shutdown warnings.
  Quota words inside the task and an actual timeout do not trigger quota fallback.
- Legacy and canonical records are repaired on read and bounded on persistence.
  Brief delivery flags, timestamps, anchors and execution status are preserved:
  loading history never reruns the worker or promotes failure to success.
- Summaries show partial outcomes and require an explicit commit label for hashes.
- Failed-run briefs carry the report and recovery instructions, not diagnostic logs.

## Verification and activation

1. Run the outcome, store, compaction, no-replay, run-budget, fallback and brief tests.
2. Render cards at desktop, narrow and phone widths; expand the mandate, result and
   technical excerpt. Confirm no horizontal overflow and a scrollable bounded log.
3. Before first activation, keep a local backup of the historical delegation JSONL
   and canonical state outside version control, since startup compaction is bounded.
4. Check the runtime is idle, restart the operational server, and reload the browser.
5. Confirm old partial results and brief delivery state survive; do not retry a real
   delegation merely to verify presentation.

Automated checks use synthetic transcripts and temporary stores, with no AI calls.
Next action after activation: reload the Bridge and inspect the corrected cards.
