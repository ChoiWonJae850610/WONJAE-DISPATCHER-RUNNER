# Shared source-writer fence — staged, not installed

This Draft adds an opt-in bridge between a trusted Linux Direct Worker parent and
an existing Windows Watch. It does not register an execution route, create a
service/queue/server, execute a provider/CI workflow, retire a runner, enroll a
credential, or enable a project. Existing Direct Worker behavior is retained
when the canonical registry has no `execution.source_writer_fence` registration.
An environment variable or prepared local profile alone cannot enable it.

## One backend, two native clients

`source_writer_fence.py` is the same standard-library implementation for both
operating systems. The proposed backend is an existing user-private WSL ext4
directory, accessed natively by Linux and through that distribution's Windows
UNC share. Real paths, projects, task scope and credential topology remain in
private runtime configuration, never this public runner repository. No NTFS
mount, credential or system configuration is installed by this change.

The root must already exist; the bridge does not bootstrap a live store. Native
UID/mode0700, exact root path/inode, backend identity and WSL boot identity must
match. Windows performs a bounded native metadata read. A boot/permission/root
change refuses admission; it never silently initializes an empty replacement.
The actual deployment needs a reviewed capability report for that exact backend
and environment. Local experiments prove the tested process/OS boundary only;
they are not power-loss or Production acceptance.

A per-project atomic mkdir transaction serializes state updates and commits a
persisted epoch/grant/consumption fence before a source operation. State uses
fsync plus atomic replacement. A losing client gets no grant. An interrupted
transaction is preserved, even after its process dies. Ownership is not inferred
from a file's existence or mtime. A persistent grant is independently checked
for project/repository/branch, Task-ID, request ID, run/job/attempt, base/policy
commits, epoch, lease token and deadline.

Request admission TTL stays at most900s; it is never renewed/restamped. A bounded
job lease may last up to the existing75-minute job timeout. The short proof given
to the Watch facade still expires within60s. Lease expiration blocks mutation
and both new writers, never authorizes takeover or task replay. This needs no
heartbeat daemon. Exact known termination/publication and fresh remote evidence
are prerequisites for explicit reconciliation. Unknown child or interrupted
transaction remains blocked for offline exact-run reconciliation; no API in this
Draft auto-deletes/reclaims it.

## Work boundaries and provenance

`registered_source_fence.py` validates canonical opt-in plus a SHA256-pinned
private configuration. Configuration is static across jobs: the trusted parent
derives the current job ID from exact run/attempt, reviewed job identity and
actual runner readback. There is no per-job ARMED/config-file approval.

`GitHubEvidenceReader` performs bounded read-only collection at actual admission,
source start and publication. It requires complete run/job/PR inventory, current
product and common-policy HEAD, exact policy/participant revisions and the
current source gate. Unknown queued/running scope is blocked; a display title,
empty PID list or stale observation is not authority. Read-only validation may
be exempted only by exact reviewed workflow identity. A pre-existing product PR
is not automatically adopted/closed or bypassed. Retry/resume needs its exact
persisted grant and reconciliation; it cannot invent a fresh task to avoid it.

Every source/branch mutator must actually participate: Direct Worker, Watch,
document merge, reconciliation, source recovery and provider-state publication.
This Draft also connects the trusted branch/state entrypoints listed in
[the reviewed participant inventory](TRUSTED_WRITER_PARTICIPANTS.md). Provider
commands/host and product official CI remain unchanged; only post-success Git
publication is isolated as a dependent job. Current live cross-route exclusion is
**NOT_INSTALLED / BLOCKED**, even after offline checks pass. Do not assert that
all participants are installed, merge the Draft or register it as live merely
because this document exists.

The core workflow acquires before Source, checks before protected state advance,
commit, push, PR creation and merge, binds the exact published PR, acknowledges
only its exact merged commit, and releases only after a known successful finish.
`run_direct_worker_recovery.py` holds the actual source child inside the shared
transaction for each bounded turn, including replacement turns. A killed parent
cannot hand its grant to Watch. A failure preserves ownership/terminal evidence.
The existing product-state protection, publication, exact-head/integrated CI,
provider boundaries, runner labels and concurrency remain in effect.

The Windows CLI uses the same backend and reader. It prepares the existing
noninteractive Git credential-helper pattern without reading any credential on
import/construction. Only GET is allowed, redirects fail closed and the fence
reader rejects Issue endpoints. No new key, credential copy or secret store is
introduced. The actual existing credential is not exercised by fixture tests.
Windows child reservation/attachment/termination uses native process creation
identity, not a PID alone; publication stays blocked while the child is pending.
Local lifecycle record checks validate the already-owned grant without GitHub
GET. Only actual source/publication boundaries collect fresh remote authority.

Drive hash-first intake remains unchanged: idle GET0, one bound Issue GET for a
new valid request. Control-plane HEAD/run/job/PR reads are separate bounded work
checks, not extra Issue reads, status publishing or an idle polling loop. No
status/Push server scope or production sender is added.

## Evidence and activation boundary

Focused tests verify grants, restart/consumption, remote/PR/job/policy refusal,
exact recovery/own-merge receipts, no-record polling, default-off behavior,
fixture/live separation and source/publication hook placement. External local
evidence separately exercises real Windows and WSL processes competing for one
backend, actual fixture Git HEAD changes, child crash and native identity.
Neither synthetic GitHub metadata nor a passing Python interface is operational
mutual-exclusion acceptance.

Before installation: review exact shared/product policy; deploy and prove every
reviewed mutating entrypoint; reconcile pre-existing product PRs without replay; preserve
Source/Provider/Manual-QA gates; validate existing protected runtime access and
the exact backend; then apply only through a separately scoped existing-Watch
normal drain/restart and one-project real Watch/direct-input acceptance. Keep
all old routes/runners until that product's actual transition is accepted.
