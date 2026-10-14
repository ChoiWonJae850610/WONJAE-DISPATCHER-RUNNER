# CLASSMO 027-P1 — Development-only private Storage + 017 schema preparation

**Status: common Runner PR preparation only.** This page is not an authenticated Owner execution, deployed executor, live 017 migration or completed 027-P validation.

## Verified input and identity (2026-10-14)
- CLASSMO registered branch: `cloud-dev-v1` at `4ea17a3d1d55298f1c86ad113b81c870a5d24363`.
- Source PR [#135](https://github.com/ChoiWonJae850610/CLASSMO/pull/135) merged; exact integrated validation [#38520259766](https://github.com/ChoiWonJae850610/CLASSMO/actions/runs/38520259766) SUCCESS.
- Existing source SQL: `supabase/migrations/20261014001700_017_editorial_media.sql`, blob `71c998b431986f340835437ea9a6f689bcbc1f3e`, SHA-256 `04c65a4fae67b2fa8eedf0c9ef8759ddab71077b0235327d05ff1fa7fad03311`.
- Exact source `SHA256SUMS` manifest blob: `a54512272d88105630ad3ebdade7145e1acd4a71`.
- Read-only Supabase `CLASSMO Development` ref `hfclkbqzxiqhcckwknih`: ACTIVE_HEALTHY; migration ledger through `016_editorial_drafts`; zero Storage buckets and zero `storage.objects` policies on 2026-10-14.

## Owner boundary
1. A separate DEV-CONTROL review registers **only** `editorial_017_storage_apply` with executor `classmo_editorial_017_storage_apply`; existing CLASSMO 016 gates remain unchanged. No new GitHub or Supabase credential names or permissions.
2. A future **product-owned docs/state PR**, not this Runner PR, must change `DECISION_REQUIRED` to `PROVIDER_GATE` with exact registered gate and keep an honest after-provider-success successor. Until that distinct Owner document action, SANJINWORKS should not display an executable button.
3. Only an authenticated SANJINWORKS Owner click may dispatch the trusted default-branch `provider-gate-approval.yml`. Trusted Runner rechecks current registry SHA, product branch/source SHA, matching product gate, exact integrated success and absence of another source writer.
4. The dedicated handler pins the existing source blob, SHA-256 and entire 17-line manifest; it requires the precise Development target, 16 prior ledger entries ending in 016, and empty bucket, policy and new-table inventories. The only additive SQL is one fixed private `storage.buckets` INSERT with 5-MiB and three exact MIME types. It sends this fixed prefix followed by unchanged 017 source in one named 017 migration API call, then independently reads back ledger, bucket settings, two restrictive storage policies and four forced-RLS tables.
5. If history/source/bucket/policy/permissions/readback differs or a partial apply is suspected, stop with a fixed non-secret error. **Never blindly replay 017**. Preserve source, evidence, original ledger and existing buckets.
6. A receipt can attest **027-P1 schema/storage configuration only**. It explicitly marks two-session race tests, actual image byte validation, EXIF stripping, cleanup verification, authenticated upload flow, device, publish and Production **NOT_RUN**. A distinct 027-P runtime/byte-validation Owner stage is required before 027-003 source or 029 Preview can be claimed ready.

No product-source writes, provider mutation, HMAC/credential rotation, new database, new bucket during PR validation, EAS/OTA, Production or KDN effects occur as part of common-system PR/CI. The supporting tests use synthetic fake clients only.
