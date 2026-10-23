# Two Linux Direct Worker slots — preparation, not activation

Status: **PREPARED / NOT ACTIVATED** (2026-10-23 KST). This document and the
associated guarded Runner workflow change do not register or run a second agent,
write a GitHub Secret/Variable value, start a product Direct Worker, change
Production, merge a PR, or alter credential scope. All such effects require their
own verified, appropriately authorized operations.

## Verified current contracts

- DEV-CONTROL/main `PROJECTS.yaml`: each registered product has
  `source_writer_concurrency: 1` and `cross_project_parallel: true`.
  **Do not** increase same-project writers or enroll common systems as products.
- Existing Direct Worker caller: `home-linux` only; reusable core has
  `concurrency.group: direct-worker-${{ inputs.project }}`,
  `cancel-in-progress: false`. Retain those values and authenticated
  SANJINWORKS Owner-only dispatch.
- The core routes to `[self-hosted, Linux, X64, direct-worker]` and currently
  permits one job per installed GitHub runner service. Adding one *separate*
  GitHub runner installation with the same labels enables at most two different
  projects at once, not two writers in one product.
- `DW_JOB_ROOT`, `DW_RUNNER` and `DW_PRODUCT` are Run-ID/Attempt-scoped.
  Local monitor entries use `/var/tmp/wonjae-monitor/<run-id>-<attempt>`.
  Each installation still needs a distinct working directory, service,
  runner name and ownership/cleanup boundary; do not share an `_work` root,
  Codex home, ephemeral checkout or credential file.
- Prior activation documentation observed one repository-scoped Linux runner,
  `WONJAE-HOME-DIRECT-WORKER` (ID 21). This is historical evidence, **not**
  a live online-count or host resource measurement.

## Why credentials need isolation

Today every Direct Worker restores `CODEX_AUTH_JSON` into its isolated
`CODEX_HOME` and runs `gh secret set CODEX_AUTH_JSON` when finished.
Two parallel jobs may have started from the same earlier GitHub Secret snapshot:
simply serializing the final writes does **not** prevent a stale refresh
credential from overwriting the newer one. A second runner sharing that mutable
credential is not a safe upgrade.

This PR prepares an opt-in credential-slot fence without changing the existing
single-runner behavior:

1. With **both** repository Variables `DW_PRIMARY_RUNNER_NAME` and
   `DW_SECONDARY_RUNNER_NAME` unset, the existing `CODEX_AUTH_JSON` read and
   refresh path stays `legacy`. Do **not** register another matching Runner
   in this state.
2. When **both** names are set and distinct, the trusted workflow selects
   `primary` or `secondary` using the GitHub-assigned `runner.name`.
   A mismatched runner or a partial/duplicate configuration fails closed.
3. The primary slot reads and refreshes **only** `CODEX_AUTH_JSON`; the second
   reads and refreshes **only** `CODEX_AUTH_JSON_SECONDARY`. Neither is logged
   or saved as an artifact. The slot code verifies that the two credentials
   contain *different* refresh tokens without logging or persisting either value.
   Independently authenticated sessions are required, **not two copies of one
   auth.json**. The same ChatGPT user alone does not prove independent sessions.
4. Every real product operation still requires a matching SANJINWORKS Owner
   action and independent exact-branch/PR/CI gates. There is no new task queue,
   Dispatcher or fallback host.

The dual-slot code is dormant until the two non-secret runner-name Variables
are configured. The optional second Secret is not read by the legacy path.

## Separate Owner-controlled activation prerequisites (NOT DONE)

1. Preserve the existing Linux runner and Windows validation installations.
   Confirm live runner inventory, GitHub repository scope, actual labels and
   online status; confirm the primary exact runner name at activation time.
2. Have the Owner separately authorize and provision a **new independently
   authenticated** `CODEX_AUTH_JSON_SECONDARY` in GitHub Actions Secrets
   through a trusted credential bootstrap. Never copy/paste its value into a
   PR, chat, local log or artifact. This PR does **not** provision credentials.
3. Set both `DW_PRIMARY_RUNNER_NAME` and `DW_SECONDARY_RUNNER_NAME` repository
   Variables to the two verified distinct GitHub runner names. Credential and
   configuration changes are **not authorized** by this preparation PR.
   These two variables and the secondary credential must be configured
   **before** registering/enabling the second runner on the shared
   `direct-worker` labels; otherwise the existing shared Secret may race.
4. Register a **second separate** Linux X64 runner installation/service with
   `self-hosted`, `Linux`, `X64`, `direct-worker` labels. Allocate its own
   `_work`, service directory and temporary paths. Keep distinct names;
   never operate two runner services from the same installation directory.
   No runner registration or service change is part of this PR.
5. Measure the home PC's actual available RAM, WSL2 memory limit/swap,
   Linux process memory and disk, Windows headroom and peak load when a
   source job builds. On a nominal 32-GB host, two simultaneous Node/Expo
   builds may be more constrained than two Codex edit turns. Choose a bounded
   WSL2 cap only after this measurement; do **not** change `.wslconfig`
   in this PR.
6. In a later separately authorized, non-Production smoke, verify both runners
   online and separately assigned, then concurrently run **different**
   registered projects through SANJINWORKS Owner controls. Check independent
   Codex authentication and rotation, absence of same-project writers,
   unique job/workspace paths, WSL2 peak memory, Runner Validation,
   exact product CI evidence, and no unintended provider/Production actions.
   The CI for this PR is static/offline; it cannot establish these facts.
7. If isolation/auth/capacity fails, disable the second runner safely *after*
   in-flight jobs conclude, retain the primary legacy route, and separately
   decide any variable/credential cleanup. Never conflate a failed Codex
   authentication or refresh with a product test PASS.

## Current PR's limited verification

The secret-free `Runner Validation` pull-request workflow exercises a pure
slot resolver and offline workflow contract tests: legacy behavior; exact two
runner-name routing; partial/duplicate/unknown rejection; distinct refresh
token admission; preservation of project-level concurrency and runner labels;
and separate refresh target names. GitHub PR-head CI proves code only, **not**
a second runner being installed, authenticated, available or performant.

## Remote-only preparation path (2026-10-23; not yet integrated or activated)

This incremental change proposes two **manual, Owner-only, trusted-main**
GitHub Actions workflows. Neither is dispatched or enabled by the PR itself:

1. **Bootstrap Secondary Codex Auth** (GitHub-hosted Ubuntu):
   workflow `.github/workflows/bootstrap-secondary-codex-auth.yml`.
   On a separately authorized Owner `workflow_dispatch` it reuses the
   existing, bounded device-code login helper in a *fresh hosted* `CODEX_HOME`.
   The verification URL and one-time user code appear in the protected job
   output; the Owner can authorize them in a browser from outside the home PC.
   Before writing, the job checks that the second Secret does not already
   exist and that the new refresh token differs from the primary session.
   **Only** `CODEX_AUTH_JSON_SECONDARY` may be created. Existing
   `bootstrap-codex-auth.yml` and `CODEX_AUTH_JSON` are untouched.
   Login on the same ChatGPT account does not guarantee independent refresh
   longevity until later live parallel smoke acceptance.
2. **Second Linux Runner Remote Setup**:
   workflow `.github/workflows/second-linux-runner-remote-setup.yml`.
   It uses the already-registered `[self-hosted, Linux, X64, direct-worker]`
   home Runner only after a separate Owner `workflow_dispatch`. The Owner
   supplies the **exact existing primary runner name** from GitHub Settings
   and a **distinct requested secondary runner name**. The default operation
   is `inspect`; there is no automatic install on checkout, PR or merge.
   - `inspect` is read-only: confirm Linux/WSL2, `systemd`, non-interactive
     `sudo`, available memory/disk and target directory availability.
     It requires the job to be allocated to the expected *primary* runner.
     It does not read or write Codex/GitHub credential values.
   - `activate` is a later, separately authorized mutating Owner action.
     It requires two distinct stored Codex refresh sessions, a confirmed
     primary online with exact labels, no queued/in-progress product Direct
     Worker, WSL2+`systemd`+`sudo -n`, free space/memory, and GitHub
     API permissions. It obtains a short-lived **repository Runner registration
     token** using the existing `OWNER_GITHUB_TOKEN`, downloads the official
     `actions/runner` release with SHA-256 verification, writes only into
     `$HOME/wonjae-direct-worker-secondary`, sets the two non-secret
     `DW_*_RUNNER_NAME` Variables, registers a **new, distinct** Linux Runner
     without `--replace`, and installs/starts its separate `systemd`
     service. It never starts a product job or changes Codex Secrets.
     Any missing dependency fails closed; a partial installation requires
     explicit Owner inspection, not blind reruns, forced reset or deletion.
   - `verify` is read-only for host/repository state: both exact runner
     names and the four registered labels must be observed as `online`,
     with expected GitHub Variable readback. This is **not** evidence
     that actual Codex parallel source jobs succeed.

### Separate activation blockers and privilege boundaries

- GitHub repository registration-token POST requires repository
  **Administration:write**; inspecting registered runners requires
  Administration:read. Variables need the token's applicable Actions Variables
  write/read scope. The existing Owner PAT's effective permissions are
  **UNVERIFIED**: never silently widen it or create a new PAT. If the bounded
  setup fails due to privileges, stop and request separate Owner action.
- The existing WSL2 Runner must be **online** to receive a remote inspection or
  activation job. Local `systemd` support, non-interactive `sudo` for
  `svc.sh install/start`, 6 GiB available Linux memory and 12 GiB disk
  are **UNVERIFIED until the inspect run**. GitHub-hosted bootstrap does not
  depend on home Runner availability.
- GitHub Variables are written in an authenticated separate Owner activation
  only. No Variable, Secret, registration token, runner installation,
  local `.wslconfig`, Production or product state is changed during PR
  preparation or CI. A non-atomic API write may leave one variable changed
  if remote access fails; the installer attempts to roll back only newly
  created names, and reports `MANUAL_REQUIRED` if rollback cannot be
  proven. If setup fails after both Variables are set, the existing
  primary Runner remains the named primary but recheck before source resumes.
- The existing Linux Runner service, product Windows Runner services and
  other repositories are never stopped, replaced or deleted by the prepared
  scripts. The new installation root must **not** already exist.
- The subsequent two-project concurrency smoke must still be started
  individually via authenticated SANJINWORKS Owner actions, after inspecting
  any unfinished runner setup; no chat button is a product run authorization.

**Current execution status: CODE/POLICY PREPARED ONLY — NOT RUN.**

## Follow-up: read-only sudo-free user service preflight (2026-10-23)

Observed, exact GitHub Actions evidence:
[Second Linux Runner inspect #39904948974](https://github.com/ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER/actions/runs/39904948974)
at integrated SHA `b9aa14a55bdf8b851bb541d3e3a4b313802b5278`:
`wsl2=true`, `systemd=true`, `capacity_ok=true`,
`memory_gib=14`, `disk_gib=952`, `target_unused=true`, but
`sudo_noninteractive=false`. The existing **system-level** installer would
fail its sudo admission. This does not establish that **user-level** services
are unavailable.

This PR extends the existing **read-only** `operation=inspect` only;
it does NOT create an install method, change `operation=activate`, dispatch
a workflow, enable lingering or modify the host:

- `loginctl show-user <current-uid> --property=Linger --value`
  reports `yes`, `no` or `unknown` (a read-only query).
- `systemctl --user is-system-running` tests the **same Unix user's**
  active user manager; `running` and `degraded` are accepted as reachable.
  Any command error, absent trusted `/run/user/<uid>` directory, unknown
  manager, or privileged/root process is recorded as a **blocker**, not PASS.
- Only static enums, booleans and capacity numbers are output:
  `user_linger`, `user_systemd_manager`, `user_runtime_dir_owned`,
  `user_systemd_accessible`, `user_service_candidate`,
  `user_service_blocker`. No login name, token, systemd environment,
  bus output, logs or secret values are copied into the result.
- Query subprocesses use argument arrays, no shell and a bounded timeout.
  They cannot run `loginctl enable-linger` or `systemctl --user enable/start`.
  `inspect` is safe to rerun ONLY after this PR is separately merged to
  trusted `main`, and after the Owner manually starts the already-known
  read-only workflow.

### Decision gate after a fresh host inspect (not yet observed)

| Observation | Bounded result |
| --- | --- |
| `user_service_candidate=true`, `user_linger=yes`, manager `running` or `degraded` | **CANDIDATE ONLY.** Prepare a subsequent reviewed, Owner-gated rootless `systemd --user` installation implementation. Do not infer persistence or install today. |
| `USER_LINGER_DISABLED` | User-manager services may stop after the last session. `loginctl enable-linger` is a separate, mutating, potentially privileged Owner decision; **never** run it from inspect. |
| `USER_LINGER_UNVERIFIED`, `USER_MANAGER_UNAVAILABLE`, `USER_RUNTIME_DIR_UNVERIFIED` or `USER_SERVICE_NONROOT_REQUIRED` | **BLOCKED / NOT PROVEN** for rootless persistent service. Keep the original runner; investigate a separately authorized privileged setup or real host evidence, not a blind retry. |

### Proposed sudo-free installation contract — DESIGN ONLY

After **successful fresh user-service preflight** and **separate Owner approval**,
a future narrow implementation could register the independently authenticated
second Runner in its own `$HOME/wonjae-direct-worker-secondary` directory,
then install a **per-user** unit under
`~/.config/systemd/user/<separate-second-runner>.service` with
`ExecStart=<second-runner-root>/runsvc.sh` and the separate Runner working
directory, using `systemctl --user daemon-reload` and
`systemctl --user enable --now <exact-unit>`. GitHub explicitly requires
custom Linux Runner services to start via `runsvc.sh`, not `run.sh`.
These are **future mutating operations**, absent from the current script and PR.

The later Owner-only installer must independently check exact GitHub Runner
identity, repository Administration/Variables permissions, offline release digest,
unique worktree/credential isolation, configured `runsvc.sh`, trusted user-manager
runtime access and lack of same-project writes. It must not replace, restart or
uninstall the first Runner or change `CODEX_AUTH_JSON`. Any partial config/
registration failure is reported for bounded recovery, not auto-cleaned.

**WSL2 limitation:** even with user lingering enabled, a systemd service does
**not** keep the whole WSL instance alive. The existing Windows/WSL startup path
remains an independent operational prerequisite; never claim reboot-autostart
from `user_linger=yes` alone.

**Current authority remains unchanged:** existing `activate` still explicitly
requires `sudo_noninteractive=true`. No new install route is authorized by
these three file changes; no Secret, GitHub Variable, privilege, Runner
registration/start or product execution has been performed. Static CI cannot
prove the home-PC user service until a future read-only `inspect` is run.

## Read-only verification failure classification (2026-10-23)

[Second Linux Runner verify #39914027755](https://github.com/ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER/actions/runs/39914027755)
ran on `WONJAE-HOME-DIRECT-WORKER-2` at trusted `main` SHA
`4616e71b6a3e2e438e8e513e360acf68c74fee30`, but exited from the
`verify` step with the **generic** `SECOND_RUNNER_COMMAND_FAILED`.
This is evidence that a CLI read failed, **not** evidence of missing token
permissions, broken Codex authentication or a nonfunctional second Runner.
The original log intentionally suppressed stdout/stderr; the precise
failed command cannot be reconstructed retrospectively.

The small `verify_second_runner` change reports these bounded **non-secret
codes** at the next separately authorized, trusted-main read-only `verify`:

| Code | Evidence established (no secrets or API error bodies exposed) |
| --- | --- |
| `SECOND_RUNNER_VERIFY_GH_CLI_MISSING` | `gh` executable unavailable on the assigned Runner's PATH |
| `SECOND_RUNNER_GITHUB_AUTH_MISSING` | Protected `GH_TOKEN` input absent or empty (never output its value) |
| `SECOND_RUNNER_VERIFY_VARIABLES_QUERY_FAILED` | `gh variable list` command failed; cause may be authorization, network, CLI/runtime or repository issue and is **not** presumed |
| `SECOND_RUNNER_VARIABLES_INVALID` | Variables response invalid JSON/shape |
| `SECOND_RUNNER_VARIABLES_NOT_READY` | Both exact Runner-name Variables did not match the requested names |
| `SECOND_RUNNER_VERIFY_RUNNERS_API_FAILED` | Runner inventory `gh api` command failed after Variables had matched |
| `SECOND_RUNNER_GITHUB_RESPONSE_INVALID` | GitHub Runner API response not valid JSON/shape |
| Existing `SECOND_RUNNER_REQUIRED_HOST_NOT_ONLINE` / `SECOND_RUNNER_LABELS_MISMATCH` / `SECOND_RUNNER_INVENTORY_*` | API read succeeded but Runner identity, labels, status or completeness failed the original gate |
| `SECOND_RUNNER_VERIFY_PASS` | Exact Variables and registered online Runner labels observed; neither live Codex dual-run nor reboot persistence has been validated |

The `verify` checks do **not** list credential content, start jobs, change
GitHub Variables, require privilege changes, re-register a Runner, inspect
Codex secrets or modify either systemd service. The generic command helper
keeps its existing sanitized behavior for `activate`, and `inspect`
is unaffected. A successful PR-head CI is static/offline only; the new codes
become runtime evidence **only after a separate Owner-approved main merge and
read-only workflow_dispatch**, neither of which is part of this preparation.
