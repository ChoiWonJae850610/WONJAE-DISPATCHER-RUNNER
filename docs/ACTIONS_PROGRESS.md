# Dispatcher Actions Progress

Dispatcher v2 workflows expose a compact, user-visible progress timeline in GitHub Actions.

The canonical phases are:

1. `WAKE` — trusted event accepted.
2. `CONTROL` — exact DEV-CONTROL identity and request guards verified.
3. `PRODUCT` — product routing, branch/HEAD, rules, and canonical reads verified.
4. `CODEX` — authenticated Codex reasoning/handoff is running or complete.
5. `WRITE` — bounded source/document mutation has been materialized.
6. `STARTED` — product Draft PR has been created and read back.
7. `VALIDATE` — exact-head required Actions checks are running or complete.
8. `INTEGRATE` — authorized integration is running, complete, skipped, or gated.
9. `RESULT` — terminal evidence has been reconciled.

Each phase reports one of `RUNNING`, `PASS`, `SKIP`, `GATE`, or `FAIL`.

The reusable `scripts/actions_progress.py` helper emits both a GitHub Actions notice and a row in `GITHUB_STEP_SUMMARY`. It accepts only fixed phase/state values plus optional exact Git SHA and PR number. Do not send credentials, tokens, private customer data, signed URLs, Gmail addresses, arbitrary task prose, or provider secrets through the progress reporter.

Progress is observability only. It never changes task authority or evidence semantics. In particular, `STARTED` still requires Draft PR readback, `VALIDATE PASS` proves only the checks run for that exact SHA, and runtime/build/EAS/device/physical evidence remains separate.
