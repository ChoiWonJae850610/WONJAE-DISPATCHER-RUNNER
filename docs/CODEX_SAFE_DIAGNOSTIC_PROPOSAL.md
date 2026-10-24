# Safe Codex diagnostic: offline evidence now, authenticated probe later

Status: **PREPARED / OFFLINE ONLY / LIVE PROBE NOT WIRED OR EXECUTED**.

This public common-Runner change must not start a product source writer,
merge a product PR, dispatch an EAS/OTA/provider operation, change a
GitHub secret, or touch Production or a real device. It does not grant
new credential/permission/workflow authority.

## Existing authenticated synthetic smoke is not no-write

`.github/workflows/direct-worker-home-codex-smoke.yml` is an explicit
trusted-main `workflow_dispatch` on self-hosted Linux. It:

1. Installs a public runner in an isolated workspace.
2. Reads `CODEX_AUTH_JSON`, restores a temporary SDK session and
   calls an external Codex model using `scripts/direct_worker_authenticated_smoke.py`.
3. Allows a model to edit a *synthetic* `source.txt` and the trusted
   script to make a local synthetic Git commit (no product push or PR).
4. On success, **writes the repository `CODEX_AUTH_JSON` Actions
   Secret** using `gh secret set` with `SECRET_ROTATION_TOKEN`.
5. Removes temporary workspace files.

A successful smoke is genuine external authentication/model activity;
it is not a static check. It can refresh or replace a credential and
can consume model usage. It must **NOT** be invoked as the no-credential-
change diagnostic; its original workflow and script are unchanged.

The ordinary source Direct Worker also has a rotation step. This PR
does not execute or change that pipeline.

## What the prepared diagnostics do safely

- `scripts/inspect_codex_timeout_evidence.py --log <trusted-job-log-file>`
  reads up to 500 KB **locally**, matches only fixed, known no-secret
  markers, and prints a category, attempt count and optional last
  phase. It does not print the raw log, source SHA, prompt, account,
  token, file path or arbitrary exception.
- `codex_safe_diagnostic.py:probe_injected_sdk(...)` implements a
  **single short staged synthetic turn observer**, but only accepts an
  injected SDK context factory. It has no CLI, secret loader,
  credential entrypoint, workflow, network connection or caller in
  production. Pull-request CI only passes fake SDK sessions and events.
- The observer requires a genuinely **empty, independent** directory,
  read-only sandbox, `deny_all` approval mode, ephemeral thread,
  no persisted history and disabled web search. Deadline is strictly
  capped at 120 seconds, with **no retries**. It examines notification
  **method identifiers and agent item types**, not response text.
- Test fixtures ensure missing credentials, SDK startup errors,
  turn-admission failures, response not yet seen, streamed agent
  progress, terminal status and unknown errors stay distinct.

The pinned `openai-codex==0.159.2` SDK exposes synchronous
`Thread.turn(...).stream()`, with `Notification.method`. The source
interface was verified at the SDK release commit, rather than
extrapolated from a different `openai_codex_sdk` package.

## Interpretation: evidence is not a root-cause verdict

| Marker/phase | What it actually establishes | What it cannot prove |
| --- | --- | --- |
| `account_lookup`, missing account | A local usable account object was not returned | ChatGPT account revocation, remote outage or token cause |
| `sdk_start`/ `thread_start` | The SDK setup or thread request did not complete | Any particular host/network/auth fault |
| `turn_submit` | Thread exists but the new turn request has not returned | Model did not start |
| `first_notification_wait` | Turn admission returned; first event is not yet seen | No token was ever computed |
| `agent_output_wait` | `turn/started` event seen, no streamed agent text yet | A model was idle, or text cannot arrive later |
| `agent_output_seen` | Nonempty streamed agent delta or completed agent message observed | Full turn succeeded |
| `turn_completed` | A terminal turn event arrived; status is checked | Product source/PR/CI/provider/device PASS |
| `PASS_NO_AGENT_OUTPUT` | SDK turn completed, but no observed streamed agent message | Absence of model response |

Existing `INITIAL_CODEX_TIMEOUT_480_SECONDS` describes an entire SDK
turn wall-clock boundary, **not the first model token**. Historical
three-timeout logs without new phase events must be reported as
`LEGACY_TIMEOUT_PHASE_UNKNOWN`. Neither an SDK timeout nor a
successful offline regression grants another product attempt.

## Separate future activation gate — NOT authorized here

A later explicit Owner decision, reviewed trusted-main adapter and
workflow are necessary before invoking a **real authenticated probe**.
That adapter must separately verify:

1. It runs only from trusted `main` on the approved self-hosted Linux
   worker, once per explicit Owner action; never PR/issue/schedule/merge
   trigger or automatic source failure handler.
2. It uses an isolated **synthetic empty** checkout with no product
   repository, product write tokens, private-control records or user data.
   SDK app-server receives a scrubbed environment, minimal `HOME`,
   and temporary private `CODEX_HOME`.
3. Authentication, if explicitly approved, reads an existing
   repository-scoped credential into an ephemeral copy. **Never** run
   `gh secret set`, a credential rotation workflow, bootstrap/login
   or GitHub secret mutation. SDK may refresh its temporary copy,
   which must be destroyed afterward and never published.
4. One fixed short prompt only, one `Sandbox.read_only`/
   `ApprovalMode.deny_all` turn, no tools or web search, maximum
   120 seconds and zero automatic retries. Real model/transport use
   must be disclosed and separately approved. The prompt must not
   contain product code, private context or credentials.
5. Only fixed enum phases, coarse elapsed time and verified exact
   Runner version are reported. No raw exception, network payload,
   chat content, token/account metadata, paths or credential values
   may be printed or sent to artifacts.
6. No product source next/retry/resume, document merge, CI dispatch,
   EAS/OTA, Provider, deployment, credential permission or runtime
   mutation may be reached from this diagnostic.

If a usable authenticated path without mutation cannot be verified,
**do not activate**. Offline evidence remains valid preparation; live
Codex authentication or service responsiveness remains `NOT_RUN`.

## Next decision

Only after independently verified live diagnostic evidence should a
separate Owner consider whether to adjust transport/SDK handling or
prepare smaller product work. No source retry follows from this PR.

## WAFL 013 one-shot synthetic Owner diagnostic — proposed, not running (2026-10-25)

Runner candidate `.github/workflows/direct-worker-codex-diagnostic.yml` and `scripts/wafl_013_codex_diagnostic.py` reuse the previously inert `probe_injected_sdk`. The independent Owner route is to be prepared in SANJINWORKS. **No PR-head CI calls Codex.** Until both common-system PRs are separately approved and integrated (and SANJINWORKS is independently deployed by the Owner), there is **no live Owner button**.

Admission checks current Runner/DEV-CONTROL/WAFL SHAs, the registered 013 SOURCE_READY identity, failed WAFL Direct Worker #40103585962, WAFL recovery PR #120, previous same-SHA diagnostic runs and active source-run conflicts. Existing Runner `OWNER_GITHUB_TOKEN` is read only for preflight; existing `CODEX_AUTH_JSON` is restored to a private temporary folder and scrubbed before the SDK starts, with no rotation or secret writeback. One fixed `OK` synthetic prompt, no product content, read-only sandbox, deny-all approvals, web search disabled, no persisted history, 120-second whole-stream deadline, zero retries. Only fixed outcome/phase flags leave the process; no model text or credential fields are logged.

Synthetic stream PASS does **not** prove the large WAFL source task can complete or justify direct source replay. A diagnostic timeout remains a recorded failure. WAFL source, PR #120 merge, Developer Cloudflare/R2/Provider, EAS/OTA/Production and credentials are unchanged. Actual model traffic and Cloudflare deployment: **NOT_RUN**.
