# Reusable registered Supabase Development migration — v1

**Implementation state:** shared trusted Runner code only, not yet activated for a
product. Uses the existing authenticated SANJINWORKS Provider Owner button.
No new Dispatcher, global scheduler, credential, Production, source work or
unbounded SQL editor is introduced.

## Exact product-owned registration

A product that already has a reviewed forward migration may opt into the
generic adapter in DEV-CONTROL/main:PROJECTS.yaml:

    execution:
      provider_gates:
        <product_owned_gate>:
          label: Reviewed Development schema-only migration
          approval_mode: sanjinworks_owner_button
          executor: registered_supabase_migration_v1
          ready_for_owner: true
          provider: supabase_management_api
          environment: development
          project_ref: <exact_project_ref>
          migration_name: <exact_new_migration_name>
          migration_path: supabase/migrations/<reviewed_file>.sql
          migration_blob: <exact_git_blob_sha>
          expected_prior_migration_name: <exact_last_applied_name>
          production: false
          destructive: false
          credential_mutation: false
          device_mutation: false

This is an illustrative schema, not an executable Owner registration. The
product's active branch must also already declare next_action.type
PROVIDER_GATE with matching gate and source-independent intent.

The allowed migration SQL file is Git-owned under supabase/migrations at
the exact registered active source SHA and must have an immutable matching
Git blob. No owner-entered SQL, URL, worktree output, indirect path,
symlink, unregistered project or unpinned SQL file is accepted.

The common Provider workflow reads the matching project-scoped secret
already mapped by its registered caller. The currently routed WAFL and
CLASSMO workflows have Supabase credentials; ESC and MUVEL do not have a
Supabase credential mapping unless separately added when actually needed.
Do not share another product's token or add credentials by default.

## Execution and validation

1. The existing Provider workflow confirms exact DEV-CONTROL registration,
   product active SHA, matching Owner gate and complete integrated source CI.
   Open source writers and stale registry/branch identities block.
2. The adapter validates migration filename, SQL exact blob and source SHA,
   then confirms the target project through read-only Management API.
3. Read the actual ordered migration ledger with the dedicated read-only
   database endpoint. Require the last migration name to match the pinned
   expected predecessor, and reject a previously applied target.
4. Apply only that exact SQL through Management API /database/migrations.
   This *is* a Development DB write and requires a distinct authenticated
   SANJINWORKS Owner click. It is not invoked by document PR/CI.
5. Re-read the ledger, require exact preserved history plus exactly one
   appended target migration, and upload the structured evidence artifact.
   SQL/credential values and raw provider error bodies are never logged.
6. This is a **schema-ledger** result, not a psql/RLS/tenant/transaction
   acceptance. Provider success may produce a subsequent state-only PR
   through the predeclared, already integrated provider-success handoff,
   followed by a separate SANJINWORKS Owner document merge.

**Failure:** A 401/403 names the denied endpoint capability. A mismatch,
ambiguous write outcome, preexisting migration, changed SHA or partial
ledger readback stops without automatically replaying the migration.
Reconciliation is read-only until a specific remedy is verified.

## Scope / solo verification

Single-owner Development iterations prefer the smallest useful tests.
The offline regressions cover exact blobs, source drift, ledger preservation,
HTTP 403, idempotent replay denial and non-Production registration.
Canonical exact PR-head and integrated-SHA Runner Validation remain required.

This v1 adapter intentionally handles **one migration per Owner action**,
without arbitrary SQL editing or a global migration batch planner.
Product-specific complex WAFL G1-S attestation and existing CLASSMO 016
provider code remain unchanged.

A reusable real database integration verifier for psql meta-commands,
independent concurrency and synthetic cleanup is a separate next common
work item. Such a verifier needs an Owner-provisioned Development-only DB
connection when the actual runtime test is activated, not for this PR.
