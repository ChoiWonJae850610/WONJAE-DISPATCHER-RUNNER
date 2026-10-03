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

## Provider Action Dispatcher v2

Provider actions are a separate no-source-diff execution class. The shared `.github/workflows/provider-action-core.yml` validates an exact private `tasks-v2/<PROJECT>/<TASK-ID>.json` record with `operation_type: provider_action`, requires the registered active branch to remain at the exact source SHA, rejects concurrent same-product `job/` PRs, and requires a successful exact-SHA product validation before an external provider action starts.

Thin product adapters keep credentials isolated:

- CLASSMO: `.github/workflows/classmo-provider-v2.yml`
- WAFL: `.github/workflows/wafl-provider-v2.yml`
- ESC: `.github/workflows/esc-provider-v2.yml`
- MUVEL: `.github/workflows/muvel-provider-v2.yml`

The common core supports compatible EAS Workflow updates, direct compatible EAS updates, and dispatch of an already repository-owned GitHub Actions workflow. EAS actions fail closed on app/native/dependency configuration changes since the declared compatible signed baseline. A missing project-scoped provider credential is terminal `MANUAL_REQUIRED`, not a reason to reuse a credential from another product.

Provider STARTED/RESULT evidence is written to the exact owner-authored runner wake issue. A provider PASS is limited to the provider stage actually executed; device receipt, iOS/iPad physical QA, Windows physical QA, Production, release, and other unperformed stages remain separate.

For `eas_workflow_update`, the runner executes from the exact product checkout
and uploads that local Expo project to EAS. It intentionally does not pass
`eas workflow:run --ref`: the exact Git SHA is independently proved immediately
before upload, while local-project mode resolves `.eas/workflows` beside the
app directory's `eas.json`.

### External provider queues

Long external queues are never held open by the launch runner. EAS Workflow and
repository-owned GitHub workflow actions capture the provider run ID and write
`## Dispatcher v2 provider QUEUED evidence` to the exact wake issue, then the
launch job exits without claiming terminal success.

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
`GITHUB_NATIVE_GMAIL_ENABLED=true`.

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
