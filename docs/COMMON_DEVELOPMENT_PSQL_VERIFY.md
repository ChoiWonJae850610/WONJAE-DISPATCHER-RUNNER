# Common Development psql verification harness (preparation-only)

## Status and purpose

A reusable trusted Runner **code module**, not a product action or deployed
Provider gate. No workflow calls this module yet. The present PR adds offline
tests and does not connect to a database or change any credential. This
feature is available to products registered by DEV-CONTROL PROJECTS.yaml
only after a separate bounded, validated product Owner-gate registration
and an authenticated SANJINWORKS Owner click. KDN is excluded.

The code is deliberately **not** an arbitrary SQL Editor. The product's
test plan and every SQL script are immutable Git-blob inputs, with one exact
source HEAD. The plan must be in supabase/tests and may refer only to pinned
supabase/tests/*.sql files. The registered provider workflow must additionally
check exact target branch, SHA, project, Development environment, authorized
effect and source CI. This PR changes none of those permissions.

## Planned product test contract

The next product-owned documents can register a JSON plan such as:

    {
      "schema_version": 1,
      "environment": "development",
      "project_ref": "<exact_product_project_ref>",
      "phases": {
        "preflight": {"path": "supabase/tests/development_guard.sql",
                      "blob": "<git-blob>"},
        "runtime": {"path": "supabase/tests/feature_runtime.sql",
                    "blob": "<git-blob>"},
        "fixture_setup": {"path": "supabase/tests/feature_race_setup.sql",
                          "blob": "<git-blob>"},
        "race_a": {"path": "supabase/tests/feature_race_a.sql",
                   "blob": "<git-blob>"},
        "race_b": {"path": "supabase/tests/feature_race_b.sql",
                   "blob": "<git-blob>"},
        "race_readback": {"path": "supabase/tests/feature_race_check.sql",
                          "blob": "<git-blob>"},
        "cleanup": {"path": "supabase/tests/feature_cleanup.sql",
                     "blob": "<git-blob>"},
        "zero_residue": {"path": "supabase/tests/feature_zero_residue.sql",
                          "blob": "<git-blob>"}
      },
      "expected_b_error": "EXACT_PRODUCT_CONFLICT_CODE",
      "variables": {"synthetic_case_id": "synthetic-demo"}
    }

The plan itself also requires its exact Git blob and active source SHA.
All eight phases are required for independent-session race verification;
without the four optional race phases it runs rollback-only runtime tests
and cleanup but does NOT claim race acceptance.

### Mandatory responsibilities of each pinned product SQL

- The **preflight** must fail before mutation unless the connected DB is the
  expected non-Production Development environment, exact migration/schema
  baseline, and a permitted synthetic fixture namespace. The generic harness
  additionally binds direct or session-pooler host/user to the exact project.
- The **runtime** must assert tenant, role, RPC, revision/replay and invariant
  requirements appropriate to the product. Prefer transaction-local
  synthetic data with ROLLBACK where possible. A SQL exit code alone only
  proves that registered assertions passed, not unasserted requirements.
- The optional **fixture_setup** prepares narrowly namespaced synthetic
  race fixtures and must refuse collisions with unrelated data.
- **race_a** must leave an open transaction holding a row lock; **race_b**
  must try the competing statement against the same row. The harness
  establishes two separate psql connections/backend PIDs, checks A is
  idle-in-transaction, confirms via a separate probe that B is actually
  blocked by A, then commits A and observes the registered expected
  success or exact conflict marker from B. Parallel start timestamps alone
  are never accepted as concurrency proof.
- **race_readback** must assert one accepted mutation/receipt, the expected
  revision and zero unintended effects, rather than simply returning SELECT.
- **cleanup** removes *only* synthetic data from the declared test namespace.
  It runs in a finally block even after runtime or race fails, once the
  product's preflight has proved the target is correct.
- **zero_residue** is an independent SQL assertion after cleanup, including
  the case where cleanup itself failed. A failed cleanup or residual-data
  assertion cannot return PASS. Do not run cleanup if preflight failed
  because the DB identity has not been confirmed.

### Connection and manual activation (not needed for this PR)

After separate Owner registration, the trusted workflow can provide the
Development-only libpq settings as private runner environment variables:

- WONJAE_DEV_PGHOST — project direct host db.<ref>.supabase.co or an exact
  dashboard-copied *.pooler.supabase.com **session** pooler host
- WONJAE_DEV_PGPORT — 5432 (transaction pooler 6543 is rejected)
- WONJAE_DEV_PGUSER — postgres for direct or postgres.<ref> for pooler
- WONJAE_DEV_PGDATABASE — postgres
- WONJAE_DEV_PGPASSWORD — GitHub Actions Secret, **never in arguments/logs**
- WONJAE_DEV_PGSSLROOTCERT — configured trusted certificate path

Connections always use libpq sslmode=verify-full. Direct connections may
require IPv6; a session-pooler connection can be used on IPv4-only runners.
The Owner may configure a Development DB Secret later; no credential is
added or changed by this PR. Neither the existing Supabase Management PAT nor
a client-side service-role key is substituted for a Postgres password.

The future trusted workflow also sets WONJAE_OWNER_PSQL_APPROVED=yes **only
after** authenticating a separate bounded SANJINWORKS Owner action. The
standalone module additionally requires --execute to make a real connection.
Without --execute it only validates exact source/plan/SQL blob inputs and
prints SOURCE_PLAN_VALIDATED_ONLY; it does not call psql or use credentials.
No unattended runner job, product provider dispatch or database test is
performed by this common PR.

## Result evidence and failure handling

Receipts distinguish preflight, script runtime, independent-session contention,
fixture cleanup and independent zero-residue readback. A Development runtime
receipt is neither Production, EAS/OTA nor device acceptance. The runner
records fixed, non-secret error codes and never prints connection strings,
SQL input, SQL stdout/stderr or test variables. On failed/ambiguous runtime,
no automatic SQL retry is allowed; require readback and Owner correction.

Offline tests mock psql and real Git blob pinning, including source tampering,
wrong project, transaction-pooler rejection, preflight abort, cleanup on failed
runtime/race and distinct backend-session lock evidence. The actual database
integration, PostgreSQL contention and synthetic cleanup remain **NOT_RUN**
until a product-owned Owner-gate stage is registered, approved and executed.

## CLASSMO G1-V adoption dependency

CLASSMO Development 016 G1-S already succeeded in Runner #38266508224, but
the current committed editorial-drafts-runtime.sql contains psql \set/\if
and a race-mode requiring independently provisioned synthetic fixture IDs.
Do NOT infer that the rollback-only runtime script provisions its own
persistent race fixtures. A later CLASSMO docs/state preparation must
supply/review exact fixture setup, a two-session race contract, cleanup and
zero-residue readback, plus dedicated Development DB connectivity. Only
after registering a true G1-V provider executor, exact CI and separate
SANJINWORKS Owner button can it run and unlock 026-003 saved draft source.
This generic code PR alone does not add a CLASSMO button.


## Registered Owner Provider G1-V wiring (2026-10-13 preparation)

The separately reviewed registered_development_psql_verify_v1 executor is
a shared Provider Gate handler in the existing trusted
provider-gate-approval-core.yml. The registered product execution-state
must match the gate, and the DEV-CONTROL registry must specify
Development-only provider: postgresql_psql, exact project_ref,
plan_path, explicitly reviewed connection_mode: direct or session_pooler,
require_independent_race: true, all side-effect exclusion flags false,
and ready_for_owner: true.
Readiness does not authorize source work or let a missing test plan pass:
the handler derives the test plan blob **from the exact current product
source commit**, then validates all SQL blobs again in Python. Its
mandatory --require-race gate fails closed before connecting if
any of the real concurrency phases/conflict expectation are missing.

The current CLASSMO caller binds two **optional, presently unconfigured**
Runner-repository Actions Secrets: CLASSMO_G1V_DEV_DB_PASSWORD and
CLASSMO_G1V_DEV_DB_CA_PEM. They are not automatically provisioned or
reused from a Supabase Management API token. The core workflow writes
the certificate only to a permission-limited Runner temp path; it
constructs the direct Development DB host/user from the reviewed ref,
sets verified TLS and passes the database password as a masked
environment variable, never CLI or evidence. If IPv6/direct DB reachability
is unsupported, stop and prepare a separate exact session-pooler routing
review (no guessed hostname or 6543 transaction-pooler fallback).

Missing Secrets block the actual G1-V execution with a clear fixed code
rather than silently changing permissions or claiming PASS. Before any
actual connection, the authenticated Owner must separately click the
G1-V Provider button in SANJINWORKS. The guarded Runner checks registry
and source SHA, integrated CI, current branch and absence of concurrent
writers. This common code PR and its CI do not connect to the DB.

A successful G1-V result attests only the registered synthetic DB runtime,
distinct-session lock conflict, cleanup and zero-residue assertions. A
separate Provider-success product document PR and authenticated Owner
merge are still required to expose the next source task.


## G1-V source plan compatibility (2026-10-13, preparation only)

The exact integrated CLASSMO source
6b3ad10edbe0999316964d68e3214393fb0c60ed contains
supabase/tests/editorial_g1v_plan.json with eight phase objects in the
shape {"path": "...", "git_blob": "<40 lowercase Git blob SHA>"}.
The earlier common harness demanded the key "blob" instead.

The shared verifier now accepts **either** the original
{"path", "blob"} contract or exactly {"path", "git_blob"} for each
phase. Both continue to run the same immutable on-disk Git-blob SHA-1
and "exact source commit:path" verification. Supplying both keys,
unknown keys, malformed hashes, changed local SQL or a stale source
still blocks before any connection. No product manifest or SQL is changed.

The integrated CLASSMO plan also declares the fixed custom setting
"classmo.g1v_authorized" with value
"CLASSMO-EDITORIAL-G1V-FIXTURE-001" under its
execution_contract.required_session_settings.
No arbitrary product-authored session settings, generic SQL editor or
libpq connection options may flow through.

Only this exact checked product ref and task identity, single matching
GUC name/value, postgres session role, on-error-stop and distinct
read-committed session expectation are accepted. The Python harness
constructs a **constant** PGOPTIONS value from its audited allowlist,
never from arbitrary plan-provided option text, and does not inherit
the caller's PGOPTIONS/PGSERVICE. All preflight, runtime, independent
A/B sessions, lock probes, readback, cleanup and zero-residue subprocess
connections inherit the same private, isolated libpq environment.
The custom GUC is not a password, deployment token or permission grant.
The database SQL itself checks the fixed GUC and trusted Development
project/environment before performing task-scoped fixture writes.

PostgreSQL documents PGOPTIONS as a libpq environment setting for
per-connection options using -c name=value:
https://www.postgresql.org/docs/current/config-setting.html

Offline regressions reproduce CLASSMO's actual manifest **shape**
with synthetic SQL files and exact new Git blob pins, including
unregistered keys, injected option strings, target/task mismatch and
environment propagation through each type of connection. The eight
actual CLASSMO phase blobs were also cross-checked against the
corresponding files at the integrated CLASSMO source SHA; the private
product SQL and full manifest are not copied into the public common
Runner tests.

This common-code PR neither invokes the Owner gate nor opens a
PostgreSQL connection. It cannot establish real SQL, independent
concurrency, cleanup or device PASS. The separate G1-V Owner workflow
still needs configured Development DB password/CA secrets and explicit
SANJINWORKS authentication when the Runner update is actually
integrated under independent approval.


## CLASSMO G1-V: explicitly pinned free IPv4 Session Pooler preparation (2026-10-13)

The CLASSMO Development project (ref `hfclkbqzxiqhcckwknih`) is on
Supabase Free. The Owner displayed the **actual** connection parameters,
not a speculative region-derived hostname:

- connection_mode: `session_pooler` (Supavisor shared session mode)
- pooler_host: `aws-0-ap-northeast-2.pooler.supabase.com`
- port: `5432` (not transaction-pooler `6543`)
- database: `postgres`
- user: `postgres.hfclkbqzxiqhcckwknih`
- TLS: `verify-full`, using the Owner-entered certificate Secret
- backend A/B: distinct long-lived PostgreSQL sessions; preserve the
  real `pg_blocking_pids` contention barrier, revision conflict and
  exact-ID cleanup/zero-residue checks.

The shared Session Pooler is included with Free and connects through
IPv4; no paid static IPv4 Add-on or tier upgrade is part of this work.
Supabase docs:
https://supabase.com/docs/guides/database/connecting-to-postgres

Two **independent common-system PRs** are needed: the trusted Runner
accepts only this predeclared project/gate/host and constructs the
pooler username from the exact project ref; DEV-CONTROL registers this
exact host for `editorial_016_runtime_verify`. Both must be separately
merged with exact integrated-SHA CI before the authenticated
SANJINWORKS Owner may click the real Provider button.

The prior Direct mode remains available for older registered gates.
Neither the Runner nor Registry may silently switch to transaction
pooling, arbitrary external poolers or an unreviewed hostname.
The verifier cross-checks the registered endpoint against the
isolated libpq environment before SQL. Secrets are never accepted as
connection-string text or CLI arguments; the two previously
Owner-entered Runner Secrets are preserved without reading or changing
them. Their correctness and TLS handshake cannot be inferred from
offline CI.

**This change is PREPARED ONLY / G1-V NOT_RUN.** No database login,
SQL execution, synthetic writes, real cleanup, credential change,
provider dispatch, paid add-on, EAS/OTA or Production work is part of
the preparation PR.
