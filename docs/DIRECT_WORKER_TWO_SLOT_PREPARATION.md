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
