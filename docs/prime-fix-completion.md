# Completing authorized fixes

Prime treats an automatic delegation result as an operational checkpoint. The
report and task status are evidence to inspect, not proof that the requested fix
is complete or failed. A partial result resumes the outstanding implementation
within the original authorization, either directly or through a focused follow-up
delegation after checking for active duplicate work.

An unavailable end-to-end check does not prevent a reviewed fix from being
committed, pushed or published when those actions were already authorized.
Prime reports implementation, publication and real-case verification separately.
It preserves unrelated changes, credentials, live backup/rollback requirements,
user pauses and priority changes. A diagnosis-only request remains diagnosis-only;
Hermes restart remains the operator's decision.

The changed layer is the model's persona and the instruction supplied with a new
automatic brief. Task lifecycle, delivery markers, locks, journal and frontend
transport retain their existing behavior. Delivered historical briefs must not
relaunch a worker, and duplicate POSTs for a running brief must still share one
job. Verify these invariants with `pytest tests/test_prime_brief_async.py -q`.

These instructions enable follow-up tool use; they do not constitute a
deterministic scheduler or guarantee successful model execution. A live partial
fix result still needs an operational check that Prime verifies its artifacts
and actually starts the remaining authorized work.
