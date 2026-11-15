# Trusted product branch writers — prepared, not installed

This extends the existing `cross_route_writer_fence_v1` implementation. It does
not grant Source, Provider, integration, CI maintenance or terminal authority.
Existing exact Owner claims, scope, workflow trust, Source/Provider/Manual QA and
exact-SHA validation remain prerequisites. No registry/configuration, workflow
dispatch, backend, credential or Watch installation is performed by this Draft.

| Trusted writer | Existing path | Shared boundaries |
| --- | --- | --- |
| Direct Worker | direct-worker-core, run_direct_worker_recovery, advance_execution_state | Existing source child, state, commit/push/PR/merge, exact integration and release |
| Document merge | document-pr-merge-core, document_pr_merge.integrate | Existing document review, exact merge and integrated validation/release |
| Recovery attachment | source-pr-recovery-docs-core, attach_recovery_docs.attach | Exact source+document PR identities; local write, commit/push, updated PR receipt/release |
| Recovery preparation | automatic-source-recovery-docs-core, prepare_failed_source_recovery.plan | Exact prior failed run; local state/docs, commit/push/new PR and release |
| Reconciliation | reconciliation-finalizer-core | Exact registered docs/state merge; integrated receipt/CI/release |
| Terminal finalization | source-terminal-core and source_pr_lifecycle | Exact failed PR close only; preserve branch/history, then release; no source/merge authority |
| Provider successor | prepare_provider_successor.rb | After the existing successful Provider job only: ref/state-file PR, exact receipt/release; never execute the next stage |
| Signed CI stage | lean-ci-owner-stage / stage_workflow | Existing signed ephemeral CI YAML write and release; no Git publication |
| Signed CI publication/recovery | lean-ci-owner-publish / publisher/recovery | Existing one-file signed Git objects/ref/PR, same grant through exact CI/merge/integration and release |
| Retired legacy Source | four existing product Dispatcher workflows | Under shared-fence registration refuse before source; never revive a retired route. Unregistered historical behavior remains unchanged |

`product_git_fence.py` is a thin boundary adapter over `SharedFence`, not another
lock/queue/server. Metadata roles use the same project namespace and epoch as
Source, but cannot use `before_source`. A metadata grant does not turn a current
Provider/Manual-QA action into Source authority. Existing handlers continue to
validate the precise permitted state transition. Approval of one exact PR never
waives an unrelated open PR. Recovery attachment may bind its exact two approved
PRs; any third competing PR still blocks.

The adapter retains one run/job/attempt/actor session across publication steps.
Every mutation boundary and release revalidates the same grant/epoch. Exact
post-publication PR receipts update only the caller's approved identity. Only a
read-back exact own integration can advance the expected remote SHA, after which
the task is terminal and only release is allowed. An uncertain command, lost
receipt, expired/stale grant or unknown termination preserves ownership; there
is no finally/unconditional release or expiry-based takeover. A completed
session cannot be reused. Recovery requires exact old-run/job/child/publication
reconciliation; a fresh local process or an empty Actions list is insufficient.

## Code participation versus installation

`writer_participation.code_inventory()` lists all reviewed writer IDs and
normalized source SHA256 pins, including trusted callers and shared boundary
code. Registered configuration requires the exact complete IDs/code map and
already-installed participant revisions. All Runner roles must name the actual
deployed Runner main. Unknown active workflows/jobs, incomplete inventories or
multiple jobs impersonating the current fenced job fail closed.

The generated inventory always says `installed:false`. It is coverage evidence,
not a mechanism to set `all_participants_installed=true`. Actual reviewed code
deployment, exact job/workflow mapping, protected private configuration access,
backend capability/identity and pre-install orphan reconciliation are still
required. Private runtime paths and deployment receipts do not belong here.

Git-only jobs can reach the same private backend using existing Linux
Direct-Worker labels **only when** the proposed existing
`SOURCE_WRITER_FENCE_CONFIG` repository variable is configured. Unset retains
the existing allocation. A variable is allocation metadata, not authorization:
canonical registration plus a matching protected configuration is mandatory.
Registered policy with missing configuration refuses writes. Provider execution
stays in its existing job/host with every original command; only its former last
Git helper becomes a dependent Git-only job. Git-helper failure does not pretend
to undo or re-run a successful Provider stage. Its former continue-on-error
boundary is retained at the Git-only job: a failed Git job/held grant remains
visible without turning an already successful Provider action into a retry.
This is not Git publication or Source completion PASS. Official product Windows CI and
the Provider/EAS/OTA implementation are unchanged.

## Reviewed exclusions

- `reconcile_recovery_docs.reconcile` only reads the prior attached commit/PR/CI
  and prints `read_only=true`; it does not attach, commit, push, merge or close.
- Reconciliation sweeper/claims, Source queue/checkpoints and terminal/mail
  receipts operate on Runner control/Issue/comment/notification metadata. They
  do not write product Git refs or the protected product execution-state file.
  Exact Product PR **closure** is separately fenced above.
- Provider action/status/terminal receipt routes do not publish product Git.
  Their external provider effects are independent authorized gates; this fence
  does not authorize, re-run or change those effects.
- Common-control PR merge admits only common-control repositories, never the
  four products. Retired control-write/event pilots are not product writers.
- Validation/diagnostics only read actual product sources or use synthetic
  ephemeral fixtures. Their caches/artifacts/synthetic commits are not product
  branch/state publication. A code-pinned read-only workflow exemption is still
  required if such a run is active during admission.
- Manual GitHub/API/git changes outside these trusted routes cannot be called
  fenced. Freeze authoring outside the reviewed participants during a bounded
  trial, and recheck remote HEAD/open PR inventory before every publication.

Drive intake is unchanged: idle GitHub GET0; one Issue GET for a fresh valid
bound signal. Bounded non-Issue policy/HEAD/run/job/PR reads happen only at actual
work/publication boundaries, not in the Watch polling loop. No status/Push scope,
new signal, Source job, Runner lifecycle or WSL setting is changed here.

## Future WSL retirement

The current backend requires WSL native identity and Windows UNC access. It
cannot survive WSL removal unchanged. Retire Linux only after every active Git
metadata/Source job has a reviewed Windows execution path, the same fence
contract has a validated Windows-native backend/identity adapter (including
atomic contention/crash/reboot tests), every old grant/transaction/publication
is reconciled, and one static configuration/backend identity is switched while
all writers are quiescent. Preserve old epochs/consumed fences as immutable
evidence and prevent dual active roots. This Draft implements no such port or
retirement and does not promote a fixture to a live backend.
