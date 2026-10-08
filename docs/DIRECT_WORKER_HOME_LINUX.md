# Persistent home-PC Direct Worker

Status: **HOME-LINUX DEFAULT / MINIMAL EXECUTION FLOW**.

The source of truth is GitHub. The home WSL2 runner is only an execution host; its
working copy is never authoritative. This minimal execution path is the default for all
present and future registered products.

## Production flow

For every registered product, Direct Worker uses the same sequence:

1. Read the current registry route from DEV-CONTROL.
2. Clone the requested product branch into fresh per-run storage.
3. Read the remote active-branch HEAD and the downloaded checkout HEAD.
4. Continue only when those two SHAs are identical for a new `next` task.
5. Run Codex in the product checkout with the standard workspace-write sandbox
   and network disabled.
6. Trusted workflow code commits and pushes the resulting work, then creates or
   reuses the Direct Worker PR.
7. Require the product's exact PR-head validation to PASS.
8. Merge that exact validated head.
9. Require the exact integrated-SHA validation to PASS.
10. Before advancing a source task directly into `MANUAL_QA`, require its structured `manual_qa` contract (`mode: owner_checkout`) and verify every declared Owner-executable path is a regular file that was actually changed by this source task. A task that still needs deployment, OTA/build, provider execution, credentials, or another source step must not transition directly to MANUAL_QA.

There is no separate starting-head CI gate. A current remote/local HEAD equality
check is sufficient to start source work.

## Deliberately removed from the production gate

The production Direct Worker does not require custom Linux isolation preflight,
special `exit 28/34/36` probes, trusted/source/state/runtime directory splitting,
chmod workspace locking, or authenticated/no-provider smoke runs before ordinary
source work.

Those older mechanisms were useful while bringing up the WSL runner but created a
larger operational surface than this single-owner development environment needs.
Any remaining smoke workflow or isolation helper is diagnostic/historical and is
not a prerequisite for product source execution.

The standard Codex workspace-write sandbox remains enabled and network remains
disabled for the source turn. Git publication stays outside Codex: trusted
workflow code performs commit, push, PR creation, merge, and exact-SHA validation.

## Home runner

### Locked npm source dependencies

The trusted workflow supplies Node 24 before source execution. For an npm product with
tracked root and workspace manifests and a modern lockfile, the trusted parent prepares
dependencies in a temporary manifest-only checkout. It uses public registry tarballs with
lockfile integrity, no inherited credentials/configuration, and `--ignore-scripts`.

A SOURCE_READY action may declare `npm_preparation` with a relative `workspace` and a
bounded `packages` list of exact npm versions. This declaration belongs to protected
product state; the public runner contains no product package list. The parent installs
the declared packages only in temporary storage, then verifies `npm ci --offline`.
Product manifests and lockfiles remain byte-identical until the source worker performs
its genuine offline installation.

Installed artifacts, a standalone Node/npm toolchain and the verified cache are copied
into ignored `node_modules/.direct-worker` storage. Codex receives only explicit safe
npm variables; offline mode and lifecycle-script suppression remain enabled. It uses
`npm install --offline --ignore-scripts --save-exact` in the authorized workspace and
does not delete `node_modules` or run `npm ci`, which would remove its prepared tools.
Temporary preparation does not prove committed installation, source completion or export.
Exact product PR-head and integrated validation remain required.

Every timeout replacement prepares the fresh committed checkout again. Preparation
failure stops before Codex and never enables source network access or provider authority.

The verified repository-scoped runner is:

- name: `WONJAE-HOME-DIRECT-WORKER`
- labels: `self-hosted`, `Linux`, `X64`, `direct-worker`
- environment: dedicated Ubuntu-24.04 WSL2 distribution

All registered source callers select `home-linux`; there is no automatic hosted
source fallback.

Windows uses the scheduled task `WONJAE-Direct-Worker-WSL2` at user logon to
keep the dedicated distribution alive. The GitHub Actions runner service starts
inside the distribution. PowerShell, an Ubuntu terminal window, Codex Desktop, and
ChatGPT Desktop do not need to remain open.

## Adding another project

Adding a normal source project must not require a new execution environment.
Register only the project repository, active branch, startup/project rules,
canonical docs, product validation workflow, and Direct Worker state path in
DEV-CONTROL. The common Direct Worker then uses the same HEAD-match -> Codex ->
PR validation -> merge -> integrated validation flow.

Project-specific provider, production, credential, device, release, or physical
operations remain separate gates. KDN remains excluded.

## Verification meaning

A product source task is complete only after the exact PR head and exact
integrated SHA both pass that product's declared validation workflow. A local
checkout, a successful Codex turn, or a synthetic smoke alone is not completion
evidence.

Historical home-runner bring-up evidence remains in Git history and Actions. It is
not a recurring prerequisite for each product task.
