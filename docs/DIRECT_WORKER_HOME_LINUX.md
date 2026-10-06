# Persistent home-PC Direct Worker: verified Linux topology

Status: **VERIFIED HOME-LINUX DEFAULT ROUTING**.
Checked 2026-10-07 KST: repository-scoped runner `WONJAE-HOME-DIRECT-WORKER`
(runner ID 21) is online on Linux X64 with the four labels below. Both real home
smokes passed at `354e4bef3dc59663b7a464f17207c54b52123bd2`; see evidence below.
All four registered source callers explicitly select `home-linux`, which is also
the reusable core's default. Official exact PR-head/integrated validation remains
mandatory. Source execution has no automatic hosted fallback.

## Selected topology and authority

Use a dedicated WSL2 Ubuntu distribution on the Owner Windows PC, with a
repository-scoped Linux X64 Actions runner registered only to this repository.
Verified name: `WONJAE-HOME-DIRECT-WORKER`.
Verified exact labels: `self-hosted`, `Linux`, `X64`, `direct-worker`.
The Owner provisioned Ubuntu-24.04; local readback confirms WSL VERSION 2 and
kernel `6.18.40.1-microsoft-standard-WSL2`. Actual smoke jobs used runner ID 21.
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
no-provider smoke receives no product, provider, auth or rotation secrets. The
separate main-only authenticated synthetic smoke receives only Codex auth and
trusted-step rotation authority, never product/provider credentials.

## Source and publication separation

Every task reserves a unique `direct-<run-id>-<attempt>` directory. An existing
directory fails closed; checkout uses `clean: false` into new paths, with Git
credentials not persisted. No product checkout/cache is reused. Pinned SDK
dependencies install in a job-owned virtual environment without a pip cache.
The task uses a separate HOME and gh configuration directory. Cleanup removes
only the validated exact job directory, including auth, control/product clones,
abandoned timeout checkouts, result/failure/body files, venv and Git auth helpers.
There are no auth/source caches or uploaded private artifacts.

The pinned Linux helper creates mount targets for missing protected paths. Trusted
code therefore prepares empty file/directory targets only when originally absent,
records their identity, and removes only the same unchanged empty targets after
SDK closure, including failure/timeout. Existing source paths are never removed.
Modified/replaced/nonempty targets fail closed; Git exclusions never hide them.
Protected symlinks are rejected rather than granting an outside read mount. The
actual sandbox regression checks that no mount placeholders survive in Git state.

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

## Owner bootstrap reference (completed for the current runner)

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
   Windows drives are mounted. If `unshare -Urn true` is blocked by Ubuntu's
   unprivileged-namespace policy, apply the existing Linux prerequisites only in
   this dedicated distribution during Owner provisioning:

   ```bash
   for setting in kernel.unprivileged_userns_clone=1 kernel.apparmor_restrict_unprivileged_userns=0; do
     key="${setting%=*}"
     if sysctl -n "$key" >/dev/null 2>&1; then
       printf '%s\n' "$setting" | sudo tee -a /etc/sysctl.d/90-direct-worker-userns.conf >/dev/null
     fi
   done
   sudo sysctl --system
   unshare -Urn true
   ```

   Jobs fail closed and never modify persistent host policy themselves. Do not
   apply these provisioning commands to a shared Linux host/distribution.
3. Open this repository's **Settings → Actions → Runners → New self-hosted
   runner → Linux / x64**. Execute GitHub's current download/extract commands in
   a fresh Linux directory such as `~/direct-worker-runner`. Do not copy the
   temporary registration token into this repository, logs, scripts or commits.
   Use the generated configuration command with
   `--name WONJAE-HOME-DIRECT-WORKER --labels direct-worker --work _work`.
   Do not use root as the runner account. Install/start the repository-scoped
   service with `sudo ./svc.sh install <linux-user>` and `sudo ./svc.sh start`.
   Confirm GitHub shows **Idle/online**, OS **Linux**, and all four exact labels.
   To keep this distribution alive after Windows logon, register a user task in
   Windows PowerShell (no stored password/token):

   ```powershell
   $dwUser = "$env:USERDOMAIN\$env:USERNAME"
   $dwAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -WindowStyle Hidden -Command "& wsl.exe -d Ubuntu-24.04 --exec /bin/sleep infinity"'
   $dwTrigger = New-ScheduledTaskTrigger -AtLogOn -User $dwUser
   $dwPrincipal = New-ScheduledTaskPrincipal -UserId $dwUser -LogonType Interactive -RunLevel Limited
   $dwSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable
   Register-ScheduledTask -TaskName 'WONJAE-Direct-Worker-WSL2' -Action $dwAction -Trigger $dwTrigger -Principal $dwPrincipal -Settings $dwSettings
   Start-ScheduledTask -TaskName 'WONJAE-Direct-Worker-WSL2'
   ```

   This starts only the dedicated distribution; its enabled runner service starts
   inside WSL. Availability before Owner Windows logon is not claimed. A Linux
   service or registered task alone does not prove reboot persistence/allocation;
   observe online status again after the next Owner logon/restart. GitHub runner
   download/version commands come from
   the live settings page, not a stale pinned archive URL in this guide.

For any future runner replacement, report only the distribution/runner name and
online status; never send registration/auth tokens. Re-read inventory and prove
both actual allocation/isolation and authenticated synthetic source gates before
changing routing. Offline/unallocated home jobs cannot silently acquire hosted
source authority. The existing `github-hosted` core option is retained only as
explicit alternate code routing; the public caller exposes no host selector and
no operational caller selects it. Using it requires separately authorized trusted
main code/policy changes. Existing strict external *static-validation* fallback
is unchanged and grants no source execution or provider authority.

## Evidence and completion

| Gate | Exact trusted main SHA | Home run / result |
| --- | --- | --- |
| No-provider isolation, checkout cleanup, 36 focused tests | `354e4bef3dc59663b7a464f17207c54b52123bd2` | [37498315724 / PASS](https://github.com/ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER/actions/runs/37498315724) |
| Actual authenticated source function, unchanged Git, trusted-parent local commit, auth rotation and cleanup | `354e4bef3dc59663b7a464f17207c54b52123bd2` | [37498500500 / PASS](https://github.com/ChoiWonJae850610/WONJAE-DISPATCHER-RUNNER/actions/runs/37498500500) |

Both jobs allocated `WONJAE-HOME-DIRECT-WORKER` / Linux with all four labels.
Preparation repair PR #230 passed 414 tests and actual Linux isolation at head
`67836a4f83ebf45aabed5d450e38bcb3d93d363b` in run 37498063048, then its actual
integrated SHA above passed canonical Runner Validation run 37498315072.
Earlier authenticated runs 37496910260 and 37497615914 failed closed on helper-
generated missing mount placeholders; cleanup succeeded and no source was
published. PRs #229/#230 diagnosed and repaired this without weakening protection.

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
manual smoke workflows exist on trusted main. The separate routing activation
uses the observed online inventory and both successful home runs above. Its
exact final head and actual merge SHA are validated independently and recorded
in the activation PR's Actions evidence and the private control document. Never
infer a product source task/push/PR PASS from this synthetic acceptance.
KDN, Dispatcher/Gmail queue, production/provider/runtime/EAS/device/physical work
are excluded. Unperformed external stages remain NOT_RUN/NOT_INFERRED.

References: [WSL configuration](https://learn.microsoft.com/windows/wsl/wsl-config),
[pinned Linux helper](https://github.com/openai/codex/blob/rust-v0.159.2/codex-rs/linux-sandbox/README.md).
