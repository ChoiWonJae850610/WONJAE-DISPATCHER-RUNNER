# Direct Worker Codex whole-turn timeout diagnostics

## Meaning of the existing timeout marker

The existing `INITIAL_CODEX_TIMEOUT_480_SECONDS` marker is **not** proof that the
model never returned its first token. In the current Direct Worker, the 480-second
Linux timer surrounds the entire SDK interaction: Codex client startup,
`account(refresh_token=False)`, `thread_start`, `thread.run`, and SDK close.
A full turn can time out even if the model already wrote uncommitted files.

Keep the original marker and three-attempt, zero-commit recovery behavior for
backward-compatible exact failed-run evidence. This change **does not** extend
the deadline, switch model, modify credentials, dispatch work or permit an
additional source attempt.

## Safe phase markers

The trusted child emits only coarse, static labels and elapsed whole seconds:

- `DIRECT_CODEX_PHASE phase=sandbox_setup elapsed_s=N`: sandbox/mount setup is entering
- `phase=sdk_start`: start of the Codex app-server/client context
- `phase=account_lookup`: client context opened; account lookup is entering
- `phase=thread_start`: account lookup returned a nonempty session; thread creation is entering
- `phase=turn_run`: SDK thread creation returned; the blocking full source turn is entering
- `phase=turn_result`: complete turn result returned; SDK context is exiting
- `phase=sdk_closed`: context exit completed

On the exact 480-second exception:

`DIRECT_CODEX_TIMEOUT_PHASE phase=<last_phase> elapsed_s=N`

A phase is the last boundary the trusted parent observed, **not** proof of what
the model was doing. In particular, `turn_run` does not prove a missing first
response, a model outage, excessive prompt size or an application code defect.
Only current exact-run logs and a separately approved, non-product diagnostic
can narrow those hypotheses.

The code prints no model text, prompts, file names, identities, tokens,
environment values, serialized credentials, HTTP headers or account objects.
No credential or provider instrumentation is enabled on pull-request CI.

## Owner recovery decision

1. Compare exact source SHA, source stage, runner main SHA and the last phase
   in the failed product job. Separate known 480-second timeout from
   authentication, usage-limit, security or unknown errors; the latter are not
   timeout-retry authority.
2. If repeated failures stop in startup/account/thread setup, diagnose Codex
   SDK/auth/service health under a separately authorized safe runtime test.
   Do not rotate or expose secrets as a debugging shortcut.
3. If repeated failures reach `turn_run`, treat the cause as unresolved.
   An already-approved smaller source stage may help, but the phase marker
   alone never authorizes task decomposition, more time, more model attempts,
   a source retry or a provider/Production mutation.
4. Preserve the same source SHA, protected execution state, PR/CI provenance,
   existing three-attempt recovery budget and explicit SANJINWORKS Owner
   buttons. No automatic new attempt is created from this observation.

The synthetic regression tests cover phase classification without a Codex
session, network request, product checkout, credential or side effect.
A green common Runner PR-head CI verifies the instrumentation, **not** live
Codex responsiveness or any product runtime/device/provider result.
