# WAFL G1-S · 006-only partial migration recovery

**Status: Common Runner preparation, not integrated/activated. Date: 2026-10-14.** This file does not authorize a product Runner run, Supabase migration, original 004/005 replay, credential update or Production.

## Exact source and live evidence

- Registered product: `ChoiWonJae850610/WAFL` `cloud-dev-v1` HEAD `edbaba54a4c2d6eb924b610a2703e87c6484db67`, exact integrated [WAFL Cloud Validation #38488861230](https://github.com/ChoiWonJae850610/WAFL/actions/runs/38488861230) SUCCESS.
- Original unmodified SQL Git blob: `004 = 09b5cf566f5b38726a68877afee4fed498b56b13`, `005 = 572938ff0109a405918ee4ff20d5212ae09b55c1`. After Owner-dispatched source repair and source integration, revised 006 SQL Git blob is exactly `9cd1929f2893b94c4aea38ba286cabd19c464c3b`.
- Read-only 2026-10-14 WAFL Development (`aqqarddfmdfyzeonfgwl`) returned `ACTIVE_HEALTHY`, six installed migration names and versions: `20261004072559 wafl_auth_tenant_foundation`, `20261004091059 wafl_auth_helper_private_schema`, `20261006011325 wafl_stage4_target_schema`, `20261007040240 wafl_stage4_post_apply_hardening`, `20261013150807 wafl_cloud_auth_004`, `20261013150814 wafl_cloud_domain_005`. No 006 ledger entry.
- Current live public relational inventory: **62/62 postgres-owned, RLS enabled and FORCE RLS**; 006-specific 7 routine objects, 2 assigned-read policies and two private tables are absent. The inspected `wafl_private` schema is not usable by anon and cannot be created in by authenticated.
- **Independent live Data API exposed-schema config readback is UNVERIFIED from the available read-only connector.** The dedicated Owner Runner must request the real Supabase Management API `GET /v1/projects/{ref}/postgrest` immediately before any write and **abort** unless public is present, `wafl_private` absent and the set is within public/graphql_public. PostgreSQL `current_setting('pgrst.db_schemas')` is not proof.

## Only allowed effect after a later independent Owner click

1. The separate DEV-CONTROL registration must pin `g1_supabase_006_only_recovery` / `wafl_supabase_g1_006_only_recovery`. The product-owned `.wonjae/execution-state.yaml` must also transition from `DECISION_REQUIRED` to exactly that `PROVIDER_GATE` in its own independently validated, SANJINWORKS-Owner-merged documentation PR. Merely merging common system PRs never runs a migration.
2. Trusted `provider-gate-approval.yml` rechecks the latest product active SHA, current integrated validation, registered control SHA, exact gate, no source writer and Owner dispatch identity. The dedicated 006 handler additionally pins source ancestry, **all three immutable Git blobs**, exact six historical ledger version+name pairs, complete source 11+51 table names and actual 62 public tables, postgres ownership, forced RLS, no grants to 005 domain tables, zero 006 RPC/private-table/policy objects, and actual Data API exposed schemas.
3. Only after **all** live preflight checks pass does the dedicated handler allow one `wafl_cloud_rpc_006` named Supabase Development migration call. The SQL bytes come unchanged from the exact Git blob; the G1 helper adds fixed `SET LOCAL` transaction-only declarations for Data API and 006A/B/C/D approval guards. No 004/005 write path is invoked.
4. Post-apply readback must find the original six ledger entries unchanged plus exactly one 006 entry, original public 62 owner/RLS table inventory, two new postgres-owned private forced-RLS relations, seven exact owner/definer/grant-checked functions, two narrowly scoped policies and unchanged safe Data API settings. Otherwise report a non-secret failure and **do not retry/replay** 006 without independent ledger/catalog reconciliation.
5. This is **G1-S schema-only recovery**, not accepted data parity or safe mobile cutover. Neon 50 named invariants, new data copy, actual RLS runtime tests, Apple account/identity bridge, R2 images, cloud PDF, EAS/OTA, Production and iPhone/iPad QA remain `NOT_RUN`.

## Operational sequence (future, not this PR)

Review this Runner PR exact PR-head CI → later separately approve Runner main integration/integrated CI → independently prepare and approve DEV-CONTROL registration → independently prepare WAFL docs/state Provider gate PR and have SANJINWORKS Owner merge it → verify exact integrated WAFL CI → *only then* click the distinct authenticated WAFL 006-only Provider action.

The obsolete combined `g1_supabase_schema_apply` 004→005→006 button must not be retried. This preparation changes no active product source or live provider configuration. No secrets are logged or changed.
