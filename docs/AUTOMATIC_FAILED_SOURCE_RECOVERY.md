# Failed-source recovery documentation PR proposal

**Status: PREPARED CODE ONLY; NOT ACTIVE ON TRUSTED MAIN.**

The product Source Writer is still admitted only by an authenticated SANJINWORKS Owner action. This proposal uses a separately reviewed, trusted-main `workflow_run.completed` route solely to prepare a product **documentation + execution-state PR** after a known exact initial-turn timeout. It does not dispatch Codex, write application source, apply migrations, call Providers, run EAS/OTA, merge product PRs or mutate Production.

## Strict evidence and scope

The only currently supported automatic plan code is `INITIAL_CODEX_TIMEOUT_EXHAUSTED`: exact trusted `Direct Worker <project> next`, original Owner actor, run attempt 1, terminal FAILED, self-hosted Direct Worker Job and the sole failing step `Run isolated Codex Direct Worker`, three sequential `INITIAL_TURN attempt={1,2,3} outcome=INITIAL_CODEX_TIMEOUT_480_SECONDS source=<same-original-SHA>` markers and `commits=0`.

The script rechecks live Runner main SHA, current DEV-CONTROL registry HEAD and project registration, current product active-branch SHA, original event/run SHA, job and run identity, no active product writer, no newer project runner and a bounded open-PR inventory. A stale, missing, ambiguous, unapproved or unrelated event does **not** create a PR. Unknown failures leave GitHub FAILED and current SANJINWORKS diagnostics untouched. Never inspect or echo arbitrary raw job log lines in public output.

A successful preparation writes exactly these **two files** on a deterministic per-run `docs/recovery-<project>-<runID>` branch:

- `docs/operations/RECOVERY-<task>-<runID>.md`, explaining proven timeout evidence, unresolved root cause, protected pending source/Provider/QA intent and required read-only Runner/Codex readiness diagnostic
- registered `.wonjae/execution-state.yaml`, deliberately changing current `next_action` to **DECISION_REQUIRED** and clearing the executable source queue until readiness is proved and the approved source tasks are safely re-scoped

No source file, previous active branch, historical build, database, provider config or original failed run is altered. The product PR awaits **exact PR-head validation** and **SANJINWORKS authenticated Owner document merge**, with integrated SHA CI afterward. It is never automatically merged. Once recovery readiness is independently demonstrated, a separate bounded docs-only stage can restore the previously approved SOURCE_READY slices; another Owner click is then required to execute product development.

## Owner approval required for activation

The new `.github/workflows/automatic-source-recovery-docs*.yml` reuses **existing** per-product private write secrets in a new post-failure trusted workflow and thus extends *where* those credentials may be used. **Do not merge this Runner PR to trusted main without explicit Owner approval of that exact per-product docs-write boundary**, even if PR-head regression tests pass. No Secret value, scope, privilege or Production setting is changed by preparing this proposal. The default Runner source workflow, its 480-second deadline, and previous failed runs are untouched.

The product-agnostic planner is deterministic. It does not diagnose the true cause of an initial-turn timeout from duration alone and does not claim that task decomposition will repair transport, session authorization or host resource errors. Future work is a truly bounded, nonsecret Runner readiness check and optional richer trusted failure-code summaries. The historical failed WAFL run does **not** auto-trigger this feature retrospectively.

## Verification and rollout

Run isolated offline pytest, Ruff, and all common Runner canonical validation on the **final exact PR head**. Test wrong project, SHA, run, commit chronology, stale/cancelled/queued runs, multiple failed steps, token/repo scope, duplicate webhook/PR branch, large logs, incompatible state, outstanding Product PR, and missing Runner identity. Subsequent main integration must also pass exact integrated-SHA CI before declaring the feature deployed. A real Owner-run failure rehearsal is distinct and must not be simulated by restarting an actual product Runner without consent.

This proposal does not automatically change SANJINWORKS Production. Existing document PR candidate discovery can display an eligible product recovery PR only when the product exact PR-head CI has passed; the Owner remains the authority for its integration.
