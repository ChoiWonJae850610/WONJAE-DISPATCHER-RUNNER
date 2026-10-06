# Persistent home-PC Direct Worker: staged Linux topology

Status: **IMPLEMENTED CANDIDATE; NOT ACTIVATED / MANUAL_REQUIRED**.
Checked 2026-10-07 KST: this repository has zero registered Actions runners.
The current production caller retains its existing hosted route until bootstrap
and actual allocation/security evidence exist. This is not a completed migration.

## Selected topology and authority

Use a dedicated WSL2 Ubuntu distribution on the Owner Windows PC, with a
repository-scoped Linux X64 Actions runner registered only to this repository.
Candidate name: `WONJAE-HOME-DIRECT-WORKER`.
Candidate exact labels: `self-hosted`, `Linux`, `X64`, `direct-worker`.
These are required registration targets, **not verified current labels**.
The existing Windows product/control runners remain separate and unchanged.

Store runner files and task workspaces on the distribution's Linux filesystem,
not Windows drive mounts. Disable Windows drive automount and executable interop
in this dedicated distribution. Do not reuse or change the Windows Actions
runner directory. Do not give the automated source process access to the human
multi-repository workspace. Public runner code contains generic topology only;
private routing and integration authority still come from current runtime input.

Each runner process has a distinct installation/work directory and executes
one job at a time. Existing per-project concurrency continues to serialize that
project; additional separately installed Linux runners can support independent
projects within measured host capacity. Never share work/temp directories.
Self-hosted runners must not receive fork/PR code-execution jobs. The production
secret-using core accepts only trusted default-branch invocation. The new manual
smoke receives no product, provider, auth or rotation secrets.

## Source and publication separation

Every task reserves a unique `direct-<run-id>-<attempt>` directory. An existing
directory fails closed; checkout uses `clean: false` into new paths, with Git
credentials not persisted. No product checkout/cache is reused. Pinned SDK
dependencies install in a job-owned virtual environment without a pip cache.
The task uses a separate HOME and gh configuration directory. Cleanup removes
only the validated exact job directory, including auth, control/product clones,
abandoned timeout checkouts, result/failure/body files, venv and Git auth helpers.
There are no auth/source caches or uploaded private artifacts.

The home source app-server environment uses an explicit allowlist and fixed Linux
PATH; GitHub/provider tokens and serialized `CODEX_AUTH_JSON` are removed before
SDK startup. The SDK alone uses its protected file credential to contact its
model service. Source tool commands have no network or access to that credential.

The pinned SDK's `Sandbox.workspace_write` legacy override permits host reads.
Its equivalent `:workspace` permission baseline is therefore inherited through
the `direct_source` named profile for home execution: root deny, minimal public
runtime read, exactly one product checkout write, protected Git/instruction/state
paths read-only, and network disabled. A legacy `sandbox=workspace_write` thread
or turn override would discard those split read restrictions; the home path
retains the named workspace-write profile on both calls. Approvals stay
`ApprovalMode.deny_all`. No full-access or weaker fallback is used. The existing
hosted path keeps its current explicit `Sandbox.workspace_write` preset.

The real pinned Codex Linux helper must pass the same restricted profile smoke
before home execution: host/auth/cache/other-product reads denied, symlink and
proc-root escape denied, cross-project/Git/state writes denied, product write
allowed and network denied. Unknown/unsupported profile or namespace behavior
fails the job. Home jobs never change sysctl or use sudo to relax host policy.
Trusted code alone commits/pushes/creates PRs and integrates an exact validated
head. Pre-push checks re-read base HEAD, competing writers and existing PR head;
pre-merge checks retain base freshness and the existing exact-head merge API.
Starting-head, final PR-head and actual integrated-SHA validation remain distinct.
`next`/`retry`/`resume`, protected state and bounded fresh timeout recovery remain.

## Owner bootstrap (one manual bootstrap operation)

1. In elevated Windows PowerShell, install WSL2 if absent:
   `wsl --install -d Ubuntu-24.04`. Reboot only if Windows requests it. Use this
   as a dedicated runner distribution; do not repurpose a distribution with
   unrelated workloads. Confirm `wsl --list --verbose` shows VERSION **2**.
2. Inside that Ubuntu distribution, provision packages before running jobs:
   `sudo apt-get update && sudo apt-get install -y python3 python3-venv git gh jq bubblewrap util-linux curl ca-certificates`.
   Use `/etc/wsl.conf`:

   ```ini
   [boot]
   systemd=true
   [automount]
   enabled=false
   [interop]
   enabled=false
   appendWindowsPath=false
   ```

   Terminate/restart only this dedicated distribution from Windows PowerShell
   (`wsl --terminate Ubuntu-24.04`). Confirm `unshare -Urn true` succeeds and no
   Windows drives are mounted. If Ubuntu AppArmor blocks user namespaces, resolve
   the dedicated distribution's host policy during provisioning; jobs fail closed
   and never modify a persistent host policy themselves.
3. Open this repository's **Settings → Actions → Runners → New self-hosted
   runner → Linux / x64**. Execute GitHub's current download/extract commands in
   a fresh Linux directory such as `~/direct-worker-runner`. Do not copy the
   temporary registration token into this repository, logs, scripts or commits.
   Use the generated configuration command with
   `--name WONJAE-HOME-DIRECT-WORKER --labels direct-worker --work _work`.
   Do not use root as the runner account. Install/start the repository-scoped
   service with `sudo ./svc.sh install <linux-user>` and `sudo ./svc.sh start`.
   Confirm GitHub shows **Idle/online**, OS **Linux**, and all four exact labels.
   Keep WSL alive while jobs run; configure Windows startup/availability for this
   distribution separately. A Linux service alone does not prove Windows-reboot
   persistence or allocation. GitHub runner download/version commands come from
   the live settings page, not a stale pinned archive URL in this guide.

Return only the distribution/runner name and online status to Codex; never send
registration/auth tokens. Codex then re-reads runner inventory, dispatches the
no-provider smoke at the current candidate SHA, confirms allocation and actual
sandbox results, and wires the four trusted callers to `execution_host:
home-linux` in a final implementation commit. Home routing has no automatic
GitHub-hosted fallback. Offline/unallocated home jobs cannot silently acquire
hosted source authority. Existing strict external *static-validation* fallback
is unchanged and grants no source execution or provider authority.

## Evidence and completion

The hosted Runner Validation runs the same actual Linux sandbox regression with
no auth/model/provider call. This checks the candidate code on Linux; it is not
home-PC allocation evidence. `direct-worker-home-smoke.yml` is dispatch-only and
uses the fixed candidate home labels, fresh checkout and exact cleanup.

After home smoke, require the trusted-main `direct-worker-home-codex-smoke.yml`
authenticated synthetic source turn on that actual home runner, plus
trusted-parent Git boundary evidence. It invokes the actual source function
against a fresh synthetic checkout, verifies only the expected source change and
unchanged Git metadata/HEAD, then commits locally only from trusted parent code.
It receives only the protected Codex credential; no product/provider token or
product publication is involved. It rotates auth only after success and removes
the exact job storage even on failure. Do not edit product source
just to manufacture smoke. Until these gates are observed, model source execution
and publication smoke are **NOT_RUN**, not inferred from CLI/unit test PASS.
The preparation PR may integrate after its exact Linux/security PASS so both
manual smoke workflows exist on trusted main. That does not activate home source
routing. The separate final activation commit requires actual online inventory
and both home smoke results. Validate the final activation PR head, merge only
on PASS, validate the actual integrated
SHA, and synchronize the private control document with those exact run identities.
KDN, Dispatcher/Gmail queue, production/provider/runtime/EAS/device/physical work
are excluded. Unperformed external stages remain NOT_RUN/NOT_INFERRED.

References: [WSL configuration](https://learn.microsoft.com/windows/wsl/wsl-config),
[pinned Linux helper](https://github.com/openai/codex/blob/rust-v0.159.2/codex-rs/linux-sandbox/README.md).
