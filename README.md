# WONJAE-DISPATCHER-RUNNER

A small open-source runner for PC-free Codex automation using the published OpenAI Codex Python SDK and ChatGPT account authentication.

## Current status

This repository is the public execution engine for validated Dispatcher v2 product automation. The original read-only and control-write pilots are complete. It does **not** contain product source, product credentials, customer data, or deployment configuration.

The first acceptance path is deliberately read-only:

1. GitHub Actions starts on an explicitly trusted manual dispatch.
2. A private control repository is checked out with a separately scoped read token.
3. A protected Codex ChatGPT session is restored into an ephemeral `CODEX_HOME`.
4. Codex runs with `Sandbox.read_only` and `ApprovalMode.deny_all`.
5. The runner independently proves the exact Git HEAD and a clean worktree before the Codex turn.
6. Codex completes an authenticated read-only turn against that exact checkout.
7. The runner independently proves the same Git HEAD and unchanged clean worktree after the turn; model prose is not repository-state evidence.
8. If Codex refreshes its session, the resulting `auth.json` is written back to the protected Actions secret.
9. The ephemeral credential files are deleted from the runner.

No product TASK, branch, PR, deployment, release, or physical validation is part of this pilot.

## Why GitHub Actions

Standard GitHub-hosted runners for public repositories are currently free and unlimited. The runner itself stays generic and public; private repositories are accessed only with narrowly scoped credentials stored as GitHub Actions secrets.

## Required repository configuration

Repository variable:

- `CONTROL_REPOSITORY`: the private control repository in `owner/name` form.

Repository secrets:

- `CONTROL_READ_TOKEN`: fine-grained GitHub token limited to read-only access to the private control repository.
- `SECRET_ROTATION_TOKEN`: fine-grained GitHub token limited to this runner repository with Actions **Secrets: write** permission.
- `CODEX_AUTH_JSON`: created by the bootstrap workflow after one-time ChatGPT device authorization. Do not create or paste this manually unless recovering a known-good session.

## One-time ChatGPT bootstrap

After the two GitHub tokens above are configured, run **Bootstrap Codex ChatGPT Auth** from the Actions tab.

The job prints only the OpenAI verification URL and short-lived device code. Open that URL, enter the code, and approve the ChatGPT account. The workflow stores the resulting `CODEX_HOME/auth.json` directly as the protected `CODEX_AUTH_JSON` repository secret; it is never uploaded as an artifact or intentionally printed.

## Read-only pilot

After bootstrap, run **Codex Read-only Pilot**. It checks out the configured control repository at an exact SHA, completes one authenticated Codex turn with `Sandbox.read_only` and `ApprovalMode.deny_all`, and independently verifies that Git HEAD and the clean worktree are unchanged before and after the turn.

The workflow does not use `pull_request_target`, and pull-request validation receives no private secrets.

## Security boundary

See [SECURITY.md](SECURITY.md). GitHub and the exact checked-out commit remain the evidence source of truth; a model response is never treated as proof by itself.

## Development

```bash
python -m pip install -e ".[dev]"
ruff check .
pytest
```

Python 3.11+ is required.


## Historical control-write pilot

The deterministic private-control write and event-wake pilots are complete and their dedicated workflows have been retired. Active Dispatcher v2 product execution reads DEV-CONTROL with `CONTROL_READ_TOKEN` and writes only the separately authorized product repository with that product's dedicated write credential. `CONTROL_WRITE_TOKEN` is not used by active runner code and may be removed from repository secrets after this cleanup is integrated.

## Actions progress view

Dispatcher v2 workflows use the canonical phases in `docs/ACTIONS_PROGRESS.md`. The reusable `scripts/actions_progress.py` helper writes compact phase/state notices and a run summary so an Owner can open the GitHub Actions run and see `WAKE -> CONTROL -> PRODUCT -> CODEX -> WRITE -> STARTED -> VALIDATE -> INTEGRATE -> RESULT` without reading raw logs. Exact GitHub evidence remains authoritative; the progress timeline is observability only.


## CLASSMO Dispatcher v2

CLASSMO has passed an exact-SHA end-to-end product run and now uses a task-generic
v2 workflow rather than a hard-coded pilot Task-ID. The Owner creates an exact
private work order at `tasks-v2/CLASSMO/<TASK-ID>.json` in DEV-CONTROL, then
opens an owner-authored wake issue whose title contains the Task-ID, exact
Control SHA, and exact CLASSMO source SHA. The workflow re-reads that record at
the supplied Control SHA, rejects stale source or concurrent open `job/` PRs,
runs Codex read-only against bounded supplied context, applies only validated
allowed-path edits, creates and reads back a Draft product PR, validates the
exact PR head, integrates only to `cloud-dev-v1` when authorized, and validates
the exact integrated SHA.

The product PR contains structured Dispatcher v2 metadata and the terminal
comment contains `## Dispatcher v2 completion evidence` so the read-only
notification observer can reconcile STARTED and terminal RESULT without becoming
an execution authority. Runtime, EAS, OTA, device, Production, release, and
other separately gated stages remain outside a source-only work order unless a
future exact work order and product rules explicitly authorize them.

## First private product pilot

The first product pilot uses the same ChatGPT-plan Codex session but keeps product-specific scope and repository topology in a private DEV-CONTROL work order. The public runner receives only an owner-created wake identity and exact SHAs, checks out the private control record and product source with separate credentials, and asks Codex for a structured unified diff while Codex remains read-only.

Before applying the diff, the runner rejects binary/rename/copy/submodule changes and any path outside the private work-order allowlist. It then commits the bounded source change, creates and reads back a Draft product PR to establish STARTED, waits for the exact PR-head product validation, performs only the explicitly authorized active-development integration, and waits for exact integrated-SHA validation.

CLASSMO requires a separate `CLASSMO_WRITE_TOKEN` Actions secret scoped only to the CLASSMO repository. The credential needs repository Contents read/write, Pull requests read/write, and Actions read access. It must not have Secrets, Administration, Workflows write, deployments, or unrelated repository access. Other products use the same per-product isolation pattern only after their own Dispatcher v2 onboarding is separately validated.


## Multi-product Dispatcher v2

The validated CLASSMO execution pattern is now available through separate
product-isolated workflows for WAFL, ESC, and MUVEL. Each workflow accepts only
its own owner-authored wake prefix, derives only its own
`tasks-v2/<PROJECT>/<TASK-ID>.json` path, checks out only its own private
product repository with that product's dedicated write token, rejects concurrent
same-product `job/` PRs, and binds integration to the validation workflow named
by the private control record.

Separate product workflows intentionally permit WAFL, ESC, MUVEL, and CLASSMO
runs to execute independently. No product token is shared across workflows.
A product is not considered migrated merely because its runner workflow exists;
DEV-CONTROL routing changes only after that product's exact v2 E2E pilot passes.

## Persistent sequential source queue

Same-project source tasks can be registered as a durable linear queue without keeping a
ChatGPT/Work session in a status loop. The shared
`.github/workflows/source-queue-controller.yml` owns queue intake and successor wake-up.
The controller reacts to owner issue creation, explicit successful-job handoff, and source
`workflow_run.completed` fallback; it does not sleep or poll for task completion.

The first task keeps the existing schema-v1 exact `source_base_sha` authority. A successor
that is registered before its predecessor finishes uses control schema v2 with
`source_base.mode: predecessor_integrated_sha` and binds the exact predecessor Task-ID,
Control SHA, Attempt, and wake issue. Only the predecessor's exact `COMPLETED` queue
terminal evidence can resolve that successor source SHA. The product workflow re-verifies
that authority before mutation and still requires the project branch to equal the resolved
exact SHA.

A successful source run records queue terminal evidence only after integration and exact
integrated-SHA validation, closes its wake issue, and explicitly dispatches the controller from a separate successful-job finalizer.
The controller dispatches the one exact successor; workflow_run is a fallback. FAILED, MANUAL_REQUIRED, CANCELLED, timeout, cleanup residue, and other
non-success outcomes do not advance the queue. Product PR cleanup and one-writer concurrency
remain fail-closed.

## Provider Action Dispatcher v2

Provider actions are a separate no-source-diff execution class. The shared `.github/workflows/provider-action-core.yml` validates an exact private `tasks-v2/<PROJECT>/<TASK-ID>.json` record with `operation_type: provider_action`, requires the registered active branch to remain at the exact source SHA, rejects concurrent same-product `job/` PRs, and requires a successful exact-SHA product validation before an external provider action starts.

Thin product adapters keep credentials isolated:

- CLASSMO: `.github/workflows/classmo-provider-v2.yml`
- WAFL: `.github/workflows/wafl-provider-v2.yml`
- ESC: `.github/workflows/esc-provider-v2.yml`
- MUVEL: `.github/workflows/muvel-provider-v2.yml`

The common core supports compatible EAS Workflow updates, direct compatible EAS updates, and dispatch of an already repository-owned GitHub Actions workflow. EAS actions fail closed on app/native/dependency configuration changes since the declared compatible signed baseline. A missing project-scoped provider credential is terminal `MANUAL_REQUIRED`, not a reason to reuse a credential from another product.

Production provider authority remains disabled by default. A product adapter may register one exact repository-owned GitHub workflow as a production dispatch allowlist. A private control record may set `provider_authority.production=true` only when it uses `github_workflow_dispatch` and its exact workflow path matches that adapter allowlist; credential/device/destructive authority remains false. ESC uses this mechanism only for its registered realtime deployment workflow.

Provider STARTED/RESULT evidence is written to the exact owner-authored runner wake issue. A provider PASS is limited to the provider stage actually executed; device receipt, iOS/iPad physical QA, Windows physical QA, Production, release, and other unperformed stages remain separate.

For `eas_workflow_update`, the runner executes from the exact product checkout
and uploads that local Expo project to EAS. It intentionally does not pass
`eas workflow:run --ref`: the exact Git SHA is independently proved immediately
before upload, while local-project mode resolves `.eas/workflows` beside the
app directory's `eas.json`.

### External provider queues

EAS Workflow and EAS build queues capture the exact provider run ID, write
`## Dispatcher v2 provider QUEUED evidence`, and immediately release the launch runner.
Only `github_workflow_dispatch` additionally checks that captured run every 15 seconds,
for at most 600 seconds. Repository, workflow ID/path, run ID, and head SHA are checked on
every read. SUCCESS automatically records PASS/COMPLETED, failure or cancellation records
FAILED, and action-required/unknown non-success conclusions record MANUAL_REQUIRED. A
pending timeout keeps QUEUED. Provider reads and runner issue writes use separate credentials;
no new credential or service is required. The launch job's 25-minute outer timeout includes
run-ID capture and notification time; it does not make EAS wait.

A later Owner-authored issue comment exactly equal to `[PROVIDER-STATUS]`
triggers the product's provider-status adapter. It re-reads the historical
private control record, recovers the queued provider run ID from the issue,
queries that exact run without creating a duplicate provider action, and writes
terminal completion evidence only after provider readback. Non-terminal provider
status remains VALIDATING. Device and physical evidence remain separate.


## GitHub-native Gmail lifecycle notifications

The runner contains an opt-in Gmail notification path that sends lifecycle mail directly
from trusted GitHub Actions instead of relying on a separate ChatGPT observer. It uses
only Python standard-library SMTP/IMAP clients and is gated by the repository variable
`WONJAE_NATIVE_GMAIL_ENABLED=true`.

Required runner repository secrets:

- `GMAIL_USERNAME`: the authenticated Gmail address used only at runtime;
- `GMAIL_APP_PASSWORD`: a Google App Password for that account.

The public repository never stores either value. The notification module adds an exact
`X-WONJAE-Notification-Key` header, suppresses duplicate sends, applies the existing
`<PROJECT>-CODEX` Gmail label, moves the exact same-attempt STARTED message to Trash
after terminal RESULT readback, and supersedes the immediately prior failed/manual/
cancelled RESULT only after a higher Attempt STARTED notification is readable.

Until the two secrets are configured and the feature variable is explicitly enabled,
existing lifecycle notification observers remain authoritative and product execution is
unchanged.

## Terminal handoff and recovery

The source controller has three entry points: issue intake, explicit workflow_dispatch, and
workflow_run fallback. A successful product job re-reads its closed merged Product PR and
exact head/integrated validation before writing queue COMPLETED. Its separate queue-handoff
job sends the existing source run ID to the controller using repository GITHUB_TOKEN with
Actions write permission. The controller checks the actual completed product job, exact
private predecessor binding, validation proof, writer release, and integrated SHA. An
already-completed predecessor resolves at successor intake without waiting for an event.

For a missed event, the repository Owner may run Source Dispatcher Queue Controller on main
with exactly one of `source_run_id` or `queue_issue_number`. This reuses existing authority.
It does not revive cancelled issues or release an already DISPATCHED claim. Explicit handoff,
workflow_run, and reconciliation are serialized by the same controller concurrency group.
The persistent successor claim includes the full predecessor identity and is written before
POST. An ambiguous dispatch response leaves the fence in place for exact inspection instead
of retrying a possibly accepted action. This is fail-closed at-most-once submission.

The provider-status fallback reuses the shared exact GitHub terminal reconciler. A terminal
checkpoint avoids duplicate issue evidence and close, while Notification-Key readback makes
mail retries idempotent and retries STARTED cleanup without moving the RESULT. Gmail header
search candidates are verified by exact header value; cleanup checks Trash and Inbox even
when a copy already existed in Trash. UID-only expunge cannot delete other marked messages.

Bounded source repair uses cumulative base-to-worktree scope, permits a later repair to
restore a required file reverted by an earlier repair, and gates integration on the final
required paths. No product authority, production workflow allowlist, credential mutation,
device action, or KDN scope is added by these infrastructure changes.

Source recovery requests the smallest COMPLETE repair of all actionable items reported
together. A trusted pre-write guard evaluates the proposed final tree rather than merely
counting changed files. It recognizes exact allowlisted paths in bounded missing-file/list
diagnostics and the optional single-line JSON marker `DISPATCHER_REPAIR_EVIDENCE=` with
`missing_paths` and `checksum_manifests` arrays. Ordinary path mentions are not touch
requirements; existing nonempty artifacts need no rewrite. This is a deliberately limited
guard, not a natural-language parser or a replacement for exact Actions validation.

For recognized migration checksum failures, or any changed SQL file with a sibling
`SHA256SUMS`, the proposed manifest must contain exactly one conventional SHA-256 entry
for every sibling SQL file, matching its exact bytes. A new migration and its manifest must
be dependency-closed before writes. A rejected plan receives expected checksum content
computed by the runner. The runner only reads outside allowed_paths; it never expands write
authority to fix an out-of-scope dependency. Reads are bounded to 512 SQL files and 8 MB.

Recognized validator/check script edits require a repository-verifiable contradiction:
the diagnostic's missing artifacts and checksum conditions are already satisfied on the
unchanged exact-head checkout. If actual source obligations remain, changing validator
diagnostics cannot satisfy the guard. Unsupported validator-defect evidence fails closed;
opaque source/test errors still use the complete-repair prompt and exact Actions oracle.
The guard does not execute product validators with credentials before commit.

An incomplete/invalid plan is rejected before file writes and may receive at most two
fresh complete replacement plans against the unchanged head, inside the same validation
repair attempt. No rejected plan commits or consumes the two-commit recovery budget. Any
unexpected checkout mutation stops regeneration. Exhaustion uses existing terminal cleanup.
The same shared repair function serves all four source workflows; the workflow recovery
limit remains two commits on the same Task-ID, Attempt, Control SHA and Product PR.

Synthetic E2E regressions in `test_source_queue_controller_regression.py` exercise A -> B
explicit handoff, overlapping fallback, immediate intake, missed-event reconciliation,
non-success/cleanup/mismatched identity rejection, and ambiguous POST fencing without a
real product dispatch. `test_github_provider_terminal.py` covers fast terminal outcomes,
identity mismatch, timeout/status fallback, launch deduplication, and exact Gmail lifecycle.
`test_repair_two_attempts.py` executes initial FAIL -> repair 1 FAIL -> repair 2 PASS in real
local Git repositories, including recovery from a required-file revert. These tests prove
controller logic; they do not claim a new production deployment or live Gmail delivery.
`test_repair_completeness.py` covers aggregate partial repairs, diagnostic-only checksum
edits, exact manifest repair, state-evidenced validator defects, already-correct paths,
atomic migration dependencies, bounded regeneration without writes/commits, and scope/read
boundaries using synthetic fixtures.

GitHub event semantics: workflow_dispatch/repository_dispatch explicitly triggered with
GITHUB_TOKEN can create runs; workflow_run additionally depends on default-branch workflow
registration and has chain-depth limits. The incident's missing workflow_run controller run
was observed in Actions history; GitHub's internal delivery decision is not exposed by the
repository API. The old code had no independent success handoff. The new route removes that
single dependency instead of assuming a particular undocumented suppression cause.
See GitHub's [triggering documentation](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
and [workflow_run reference](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run).


Each read-only source repair-plan turn is bounded by a Linux main-thread 480-second
wall-clock deadline. Timeout closes its fresh SDK session and enters the existing same
repair pre-write regeneration loop: at most two replacements, no rejected writes or
commits. Committed validation repair maximum remains two; the outer job stays at 60 minutes.

Product Dispatcher Terminal Guard and reusable Source Terminal Finalizer Core own
external terminal reconciliation after source job failure/cancel/timeout. Triggers are
workflow_run.completed, explicit non-success handoff and exact Owner orphan reconciliation.
Each product workflow persists an exact run/PR/head checkpoint before STARTED mail and
updates it after each repair push. Verify exact workflow path/run/repository, queue claim,
private Control SHA, resolved Source SHA, project/task/attempt/revision, target and PR/head
before mutation. Dynamic run names are not workflow identity. Never search/adopt another PR.
A legacy orphan needs an Owner comment on its exact wake:
`[SOURCE-TERMINAL-RECONCILE] run=<RUN-ID> pr=<PR-NUMBER> head=<40-CHAR-SHA>`.
It never creates execution authority. Close only the exact unmerged failed PR, preserve
branch/history, reconcile one terminal checkpoint and close/read back the exact wake.
Deliver native Gmail RESULT by exact Notification-Key, read it back, then Trash only its
matching STARTED and verify Trash present / Inbox absent. Preserve partial-cleanup residue.
Retries must not duplicate comments, mail or close mutations. Never downgrade source
completion after a successful product job. KDN, source/provider/device writes, integration,
new attempts and ChatGPT foreground polling remain outside finalizer authority.



### Complete repair and bounded publication readback

Known aggregate missing-file diagnostics, including `gh run view --log-failed`
job/step/timestamp formatting, are checked on the virtual final tree before writes.
Unsafe, ambiguous or out-of-scope explicit paths fail closed. Partial plans use the
existing same-repair replacement limit; committed repairs still have a maximum of two.

All four source workflows call the shared checkpoint helper. Before push, it persists
one exact local direct-child publication intent on the same identity comment. After
push, it checks remote parent history and OPEN/unmerged PR identity, waiting only for
the known prior head to become the exact repaired SHA (5-second spacing, 90-second
bound). It updates/readbacks that same comment and PR before fresh exact-head validation.
Timeout reports expected/observed heads; every other identity mismatch fails immediately.
Actual terminal cancellation uses the recorded intent and verified branch for exact PR
cleanup, never validation or merge. Active repair is not terminal authority. Integration
still requires PR-head PASS and integrated-SHA PASS. No KDN or product retry is involved.

Regression coverage includes `test_repair_completeness.py`, `test_source_checkpoint.py`
and `test_source_recovery_lifecycle.py`. The latter executes the actual four workflow
shells with real local Git commits/push/merge and simulated Actions/PR transport.
These synthetic proofs do not claim live product/runtime/device completion.

### Source execution reliability

Initial and repair planning use fresh read-only SDK sessions with 480-second turn
deadlines. Narrowly recognized model-capacity/transport errors share the existing
two replacement candidates with invalid/incomplete plans; a clean exact HEAD is
required before every candidate. Transient errors back off 10/20 seconds. Authentication,
usage limits and unknown failures stop; committed repair maximum remains two.

The four source workflows share an Actions REST observer. Only an exact completed
failure authorizes repair. Pending runs wait within the unchanged 60-minute job;
cancelled/timed-out/manual/unknown results and identity/read failures stop without
repair. Exact run/repository/head-repository/workflow/event/SHA checks apply to every
read. Recognized GET-only transport/429/5xx failures have a 90-second consecutive
recovery window; no annotation reads or mutating dispatch retries are added.

Automatic terminal fallback ignores skipped/non-source events. Provider fallback
paginates exact Owner issues and trusted bot comments, binds lifecycle identity and
lets terminal completion supersede historical QUEUED for existing idempotent mail.
FAILED/manual provider status comments carry full identity. No live provider or mail
operation is part of the regression tests. Git paths are NUL-delimited; required
reads and text/binary writes reject symlink aliases and escapes, with batch binary
target preflight. Original scope, exact validation, queue fencing and KDN exclusion remain.

Regression coverage: test_readonly_plan_recovery.py, test_source_validation.py,
test_source_recovery_lifecycle.py, test_source_terminal_cli.py, test_provider_guard.py
and test_product_path_boundaries.py. Tests use synthetic Git/SDK/Actions/provider
transport and actual workflow shell snippets; they do not run product tasks.


## Direct Worker transition

The repository also contains a smaller Direct Worker route for registered products that
have migrated away from Dispatcher v2 source intake. It is intentionally independent of
tasks-v2 control records, wake issues, persistent source queues and Gmail lifecycle mail.

`.github/workflows/direct-worker.yml` currently exposes CLASSMO, ESC and MUVEL. Each
project keeps a dedicated write credential and the reusable core receives only the selected
project's token. The workflow reads the current DEV-CONTROL registry, checks out the selected
product, runs Codex in a network-disabled workspace-write sandbox with a scrubbed environment,
then lets trusted workflow code publish the branch/PR. A successful exact PR-head validation
is merged to the registered active development branch and followed by exact integrated-SHA
validation. A failed validation leaves the PR open for the `retry` command.

Direct Worker remains source-only. Provider/Production/deployment/payment/credential/signing/
device/physical/destructive actions remain separate gates. WAFL, CLASSMO, ESC and MUVEL are
registered Direct Worker products; KDN remains excluded. A registered
`execution.state_path` takes precedence over the older DEV-CONTROL handoff snapshot.
The trusted runner requires exact active-HEAD push validation before a new `next`,
protects the product state from Codex writes, and commits the predeclared source-success
transition in the same Product PR so no separate ChatGPT handoff refresh is required after
source integration.

For bounded reconciliation PRs that only synchronize product canonical documentation and
the protected execution state, **Reconciliation Finalizer** performs exact PR-head
validation, exact-head merge and exact integrated-SHA validation. Owner issue events are
the normal wake. **Reconciliation Sweeper** runs every five minutes as a fallback and
workflow-dispatches the same finalizer only when one exact open Owner-authored
reconciliation issue exists for that project and has no STARTED/PASS/FAILED finalizer
evidence. This fallback adds no source-task, provider, Production, device or KDN authority.
Runner `main` pushes also wake one sweep so newly integrated control-plane fixes do not wait for the periodic fallback.

## External product validation

`.github/workflows/external-product-validation.yml` can reproduce the registered static
checks for WAFL, CLASSMO, ESC and MUVEL at an exact private product SHA on this public
runner. Owner-authored issues use the exact title
`[EXTERNAL-VALIDATE][<PROJECT>] <40-char-sha>`.

The workflow uses the selected product credential only for exact checkout, keeps
credentials out of the product worktree, verifies the checkout/postcondition SHA, and
records structured PASS/FAILED evidence on the request issue. This evidence is explicitly
`canonical_product_workflow: NOT_SUBSTITUTED`: it does not pretend that a failed or
unstarted product-repository GitHub Actions run passed, and it grants no provider,
Production, credential, device, physical or KDN authority.
