# Enrollment controls

The administrator can inspect a local Hugging Face checkpoint or an OpenRouter
model, add it stopped, and explicitly start/resume or stop collection. All writes
require the existing GitHub allowlist, session, exact Origin and CSRF token.
Inspection generates no model responses. The fixed enrollment protocol remains
800 responses (640 enrollment, 160 validation), plus four preflight observations.

Local inspection pins the repository revision and selected native weights. It
reports staging space separately from retained response/fingerprint storage and
estimates runtime only when comparable completed runs exist. The scheduler checks
free storage and cooperative GPU locks; queue wait and downloads are separate.
Only pipeline-owned staged weights are eligible for deletion, after verified
completion. Existing caches and completed responses are retained.

API inspection freezes a compatible endpoint with temperature, top-p and output
length controls. Models/routes are never silently substituted. BYOK uses provider
keys configured in OpenRouter, not keys uploaded to this website. Before adding a
BYOK job, configure a prioritized provider key, disable shared-capacity fallback,
and confirm these settings. The collector pins that provider and verifies
`usage.is_byok` on every response, stopping on mismatch. A failed BYOK check can
still incur the cost of its first request. BYOK requests run sequentially so that
an incorrectly configured account does not dispatch a batch before verification.
See [OpenRouter BYOK](https://openrouter.ai/docs/guides/overview/auth/byok).

The editable API budget is a lifetime ceiling for this collection run, not a
fresh allowance on every save. Before every request, a SQLite write transaction
reserves conservative cost against reported charges and unresolved reservations.
BYOK includes upstream inference at reported reference cost, or list-price token
estimates when absent, plus OpenRouter charges. This is a spend-control estimate,
not the provider's invoice. Failed/timed-out requests retain reservations because
they may still be billed. Increasing the ceiling allows subsequent requests;
lowering it below committed spend blocks new requests. Existing charges are never
removed, and already dispatched requests may finish after a budget change.

Stopping a local job signals only the verified worker PID, retaining completed
observations. API stop is cooperative: in-flight requests can finish and are saved;
further reservations stop. A download already in flight may finish before its job
becomes idle. Stop Collection also prevents new work; Resume Collection preserves
individual job stops. Budget changes use a version check to prevent lost updates.
Actions and budget edits record numeric administrator ID, time and target.

## Server setup

Set `DNA_ADMIN_PYTHON` to the collector Python environment and retain
`DNA_ADMIN_RUN_DIR`. Optionally set `DNA_ADMIN_CONTROL_SCRIPT` to a trusted copy of
`scripts/distdna_enrollment/admin_control.py`. Never accept these paths from a
browser request. Start `admin_supervisor.py --run RUN_DIR` using that Python.
The supervisor uses exclusive controller locks and resumes verified archived
workers. Upgrade running controller code at a controlled restart; GPU workers keep
their existing locks and saved records. Existing completed archives are unchanged.
The operational API wrapper is recorded separately from frozen sampling sources.
Keep Hugging Face and OpenRouter credentials in the existing private run-specific
files. The default budget remains the run's existing ceiling (75 USD in preview).

Test with `python -m unittest discover -s test -p 'test_admin_*.py'`, the existing
full-catalog tests, admin tests and TypeScript checks. Browser QA must use isolated
fixtures for writes; never launch paid jobs or stop a real research job as a UI test.
