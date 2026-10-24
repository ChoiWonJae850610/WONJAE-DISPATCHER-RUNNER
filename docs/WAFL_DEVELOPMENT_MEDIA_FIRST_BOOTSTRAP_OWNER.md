# WAFL isolated Cloudflare Development first setup — Owner-only executor (preparation)

**State: CODE PR ONLY.** New executor `wafl_development_media_bootstrap_v1` is not executable merely because this PR passes. Integration, registry enablement, product execution-state selection, and a separate authenticated SANJINWORKS Owner click must all precede any live request. This document is not consent to provision, deploy, change credentials, spend money or touch the incumbent resources.

## Exact scope and resource ownership

- Product: WAFL `cloud-dev-v1`, immutable supplied source ZIP/commit from the current integrated SHA. No source-writing Runner, no new WAFL code, no SQL/Neon/Auth/Apple, no OTA/device/release.
- **Create new only:** private R2 bucket `wafl-media-dev`, Worker `wafl-r2-dev` from canonical `cloudflare/r2-upload-worker.js`, Worker `wafl-pdf-dev` from canonical `cloudflare/pdf-generator-worker/src/index.js`. Both Workers use deliberately separate `*.workers.dev` non-production names; the Cloudflare dashboard may still call that Worker's deployment environment *Production*.
- Image Worker bindings `R2_BUCKET` -> new bucket, `IMAGES` -> existing Cloudflare Images product entitlement and **new** `R2_WORKER_UPLOAD_SECRET` encrypted Secret. PDF Worker bindings `BROWSER` Browser Rendering, `WAFLOW_PDF_GENERATOR_TIMEOUT_MS=30000`, **new** `WAFLOW_PDF_GENERATOR_TOKEN` encrypted Secret.
- Existing `peacebypiece-r2-upload`, `waflow-pdf-generator`, `peacebypiece-files`, their live bindings/objects and all current URLs/traffic remain untouched. Existing screenshot-exposed legacy signing value is NEVER read, copied, echoed or reused. Legacy-key rotation is a separate independently approved Owner security operation.
- Fresh independent cryptographically generated signing/token values are written through process stdin to the exact newly created Worker Secrets and to exact future media-probe GitHub Actions Secret names. They never enter Git history, workflow logs, URL parameters, command arguments, action artifacts or Markdown. Cloudflare management token and existing GitHub Owner PAT remain in protected Actions Secrets; changing/rotating those values is not authorized.

## Gate and credential authority

- Runner executor: `wafl_development_media_bootstrap_v1`, gate `development_media_bootstrap`. The DEV-CONTROL gate must be strictly WAFL Development, `ready_for_owner:false` on initial registration; `production:false`, `destructive:false`, `device_mutation:false`, **`credential_mutation:true` only for newly generated and narrowly named Development Worker and Runner Secrets**. `requires_owner_session:true`.
- **Credential mutation is real.** It is explicitly different from the pre-existing `development_media_transport_probe` (readback + one synthetic R2 object + ephemeral PDF only, `credential_mutation:false`). Do not toggle the existing media probe's `ready_for_owner:false` before actual Worker/bucket/secret parity has been independently observed.
- SANJINWORKS must preserve its normal rejection of `credential_mutation:true` for **all other** projects/executors and must require its existing authenticated Owner POST. No chat button is a Runner execution or self-approval.
- The future control registration must be changed to `ready_for_owner:true` by a **separate scope-aware Owner approval** after exact merged Runner/SANJIN implementation, account identity, GitHub PAT Actions-Secrets-write permission and quotas/cost entitlement are checked. WAFL product `.wonjae/execution-state.yaml` remains `DECISION_REQUIRED` until a later docs/state-only product PR, integrated through SANJINWORKS Owner, selects this one `PROVIDER_GATE`.
- The future trusted Workflow additionally requires scoped `WAFL_R2_IMAGES_ENTITLEMENT_APPROVED=yes`, `WAFL_PDF_BROWSER_ENTITLEMENT_APPROVED=yes` and `WAFL_MEDIA_DEV_BILLING_APPROVED=yes`. These are Owner entitlement attestations, not automatically assumed Free or silently granted by this PR.
- The existing `OWNER_GITHUB_TOKEN` is reused as an **identity reference**, not changed. It must be independently authorized for Actions repository Secret reads/writes. If it lacks permission, the first setup fails BEFORE any new Cloudflare resource is created (writes of new Runner Secret names happen before bucket/Worker creation). Do not weaken or bypass permissions.

## Future bounded Owner run and recovery

1. Exact SHA: Runner main + DEV-CONTROL integration, WAFL current `cloud-dev-v1` and registered product validation, no concurrent product writer. Exact reviewed gate metadata and work-order identity must match.
2. Credentials/quotas: existing Cloudflare account ID/token, Cloudflare Images and Browser Run account-level permitted entitlements, explicit potential billing approval and GitHub protected secret scope are checked without sending values to the output.
3. Strict existence check for **all three named resources** plus **all new GitHub Actions Secret names**. Any pre-existing target/collision, unavailable inventory, unknown pagination or stale source is a non-retryable **MANUAL_REQUIRED** until independently reconciled. Never adopt, overwrite, redeploy or delete an incumbent object.
4. Validate two explicit Wrangler configs and PDF npm dependencies with dry runs. GitHub named Secrets are created using `gh secret set` stdin; then recheck Cloudflare targets immediately before creating the bucket and deploying the two entirely new Workers and their secrets. Verify their exact binding names/types and bucket identity readback; report only resource names and SAFE status classifications.
5. A partial run **may** leave new scoped GitHub Secrets and/or newly created isolated resources if provider networking fails mid-sequence. A rerun must **stop at existence checks** rather than overwrite; no automatic destructive cleanup or legacy restoration. A separately scoped recovery will inspect exact new-only objects/secret metadata and decide how to reuse or retire them.
6. Do not infer image bytes or PDF functionality from the setup run. The separately registered media transport probe must then be activated, tested, and only afterward real WAFL Apple/Label/WorkOrder authorization, iPhone upload/relaunch and cloud PDF open/Share/revoke are accepted.

## PR and CI boundary

- This PR modifies only trusted common Runner workflow/scripts/tests and this non-secret document. Its normal Runner Validation is static/offline; no Cloudflare API call, GitHub Actions secret write, Worker creation, bucket creation or billing mutation.
- No existing source PR (#111/#113), Runner #334, DEV-CONTROL #324, WAFL #116 or their validated merged SHA is rewritten. Product state, external Services, Production/App Store are unchanged until the future independently authorized Owner action.
