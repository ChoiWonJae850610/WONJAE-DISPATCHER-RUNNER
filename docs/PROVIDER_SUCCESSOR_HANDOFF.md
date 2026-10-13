# Registered Provider Success -> Product Next Owner Action

## Scope

This is an optional, reusable continuation contract for products enrolled by
DEV-CONTROL/main:PROJECTS.yaml (currently WAFL, CLASSMO, ESC and MUVEL).
KDN is excluded. It extends the existing Owner-first GitHub and SANJINWORKS
flow; it is **not** a Dispatcher, new scheduler, automatic next-run queue or
a new independent lifecycle database.

After a **successful** registered non-Production provider gate, the trusted
Runner may prepare exactly one *state-only product documentation PR* from an
already predeclared next action. The PR is never auto-merged. A separate
authenticated SANJINWORKS Owner document-merge click and exact PR-head and
integrated-SHA CI are required before the successor becomes executable.
The successor itself requires its own separate Owner click.

No automatic Product/SQL/EAS/OTA execution, credential rotation, role
escalation, Production, destructive data, or release work is introduced.

## Product-owned declaration

The product records its exact near-term successor in its existing
.wonjae/execution-state.yaml **before** starting the provider. For example:

    schema_version: 1
    project: CLASSMO
    next_action:
      type: PROVIDER_GATE
      title: Exact current provider operation
      source_task_id: null
      owner_action: Apply the approved Development schema
      source_scope: []
      gate: editorial_016_schema_apply
    after_provider_success:
      type: PROVIDER_GATE
      title: Independent Development database runtime checks
      source_task_id: null
      owner_action: Run registered G1-V checks through SANJINWORKS
      source_scope: []
      gate: editorial_016_runtime_verify
    after_source_success: null
    source_success_queue: []

That sample successor is **not currently registered** and must not be inserted
into CLASSMO as an executable action until an actual safe test executor,
provider admission, credentials and runtime evidence path exist. The existing
CLASSMO G1-S success occurred before this feature and had no predeclared
successor, so a past PASS does **not** retroactively start or generate a PR.

For source successors, set after_provider_success.type to SOURCE_READY and
declare a valid bounded after_source_success (and source_success_queue only
where an existing valid bounded source-to-source series requires one).
This merely registers ordinary source work; it cannot execute it.

## Runner admission and effects

- The invoking workflow revalidates the exact product/Control SHA, project
  active branch, provider registration, source CI, open source PRs and executor.
- Only after the actual provider steps and evidence uploads succeed, the
  generic successor preparation runs.
- Require a current, exact PROVIDER_GATE matching the completed gate; both
  the current operation and any subsequent provider gate must be explicitly
  non-Production and non-destructive. The successor provider executor must
  be registered, Owner-ready and not approval-only.
- Require exact current product SHA and no competing open product PR.
- Using the existing scoped product token, create a new docs/provider-success-*
  branch from the exact source SHA with **only** the existing protected
  execution-state file advanced to the already-reviewed successor. Never
  update the active branch or merge in the same provider run.
- Preserve an existing PR (no duplicate creation). If PR preparation fails,
  report a warning with the provider result remaining separate; **do not**
  replay the already-performed provider operation to retry documentation.
- The SANJINWORKS document-merge verifier remains the only product integration
  authority for this PR and independently requires exact CI and changed paths.

## Minimal single-owner verification

Focused dry-run transformation and identity/fail-closed tests, followed by the
repository's already-required Runner Validation on the exact PR head. No new
repetitive QA form or duplicate chat approval. Provider runtime/data mutation
is separately authorized per Owner button; static CI is never substituted for
actual runtime/DB readback.

## Remaining tasks

1. A general reusable SQL apply adapter can accept a repository-checked,
   exact Git-blob-pinned Development migration plus allowlisted readback
   expectations, using the same scoped Supabase Management API credential.
   Do not expose an arbitrary writable SQL console through SANJINWORKS.
2. A reusable *psql* integration verifier needs an independently verified,
   dedicated Development DB connection and a fixture/transaction/session
   harness. Race tests cannot be proven by one Management API SQL POST.
   This is required before CLASSMO G1-V can be registered ready.
3. SANJINWORKS can explicitly label completed gates with no declared
   successor as "next-stage registration needed" rather than product-wide
   completion. Failure/recovery and manual QA buttons remain separate.
4. Future source/provider successor scopes must be prepared in the
   applicable **product repository** with Owner merge; the common Runner
   does not infer product requirements from a historical roadmap.


## Provider -> SOURCE_READY -> next source terminal (2026-10-13)

A source task that ends at an independent Provider gate consumes its own
after_source_success. Do **not** reuse that now-null terminal as the follow-on
source task's after_source_success. When a Provider gate has a predeclared
after_provider_success SOURCE_READY, the product may additionally declare:

    after_provider_source_success:
      type: DECISION_REQUIRED
      title: Pending separately scoped post-source provider/media registration
      source_task_id: null
      owner_action: Prepare bounded next Owner registration
      source_scope: []
      gate: null
    after_provider_source_success_queue: []

After a verified Provider PASS, the generic document-only handoff copies
this separately declared, reviewed follow-on terminal into the emitted product
execution-state PR and clears the staging keys. Existing direct
after_source_success declarations remain backward compatible where they were
not consumed by a preceding Source stage. Conflicting or invalid terminals
are rejected before opening any product PR. Each new source remains an
authenticated separate SANJINWORKS Owner click and canonical CI event.

For CLASSMO, after its *future* G1-V PASS this can make 026-003 source work
READY once a new **state-only product document PR** has passed its own CI and
the Owner merges it. It does not backfill G1-S's historical SUCCESS.
