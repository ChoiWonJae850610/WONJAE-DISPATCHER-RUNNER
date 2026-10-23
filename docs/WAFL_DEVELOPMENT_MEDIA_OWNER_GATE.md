# WAFL Development media transport — bounded Owner executor preparation

**Status: Runner source PR only; no deployment, media request, DB write, credential setup or Owner activation.** Product `cloud-dev-v1` remains at its independently verified current SHA; `WAFL-IMAGE-012A` / `PDF-012B` are retained, not rebuilt. The existing `wafl_r2_preview_deploy` executor **deploys** an incumbent Worker; it is **not** evidence of a signed image upload or of PDF rendering. Do not reuse it as a media acceptance action.

## Exact new gate contract (not armed by this PR)

- Registry key: `development_media_transport_probe`; executor: `wafl_development_media_probe_v1`; provider: `cloudflare_development_r2_pdf`; environment: `development`; project ref: `aqqarddfmdfyzeonfgwl`.
- Effects **only on future separate authenticated SANJINWORKS Owner click**: read the existing exact Development Worker/bucket/Images/signing-secret binding identity via the pinned Wrangler readbacks; verify a second **distinct non-Production** PDF Worker has Browser Rendering and PDF token bindings. Then, and only then, issue signed PUT/GET of one unpredictable newly absent synthetic PNG key, compare its raw SHA-256, delete that **exact newly owned key** and verify 404. Send a static, secret-free HTML request to the PDF Worker: unauthenticated must return 401, authorized token must yield a bounded well-formed `%PDF-…%%EOF` byte stream. No other R2 object/tenant row is read or changed. No deploy, no Worker/bucket creation, no SQL, no real user Apple session, no provider retirement, no Preview or Production.
- The exact Workflow admission checks current GitHub integrated-source SHA/CI, absent source writer, approved gate name and `ready_for_owner: true`, session-only route and strictly fixed effects. Any binding/worker ref/name/secret mismatch or error **fails closed before the PUT**. Upload may leave an unconfirmed canary if Cloudflare becomes unavailable between PUT and cleanup; a cleanup failure is explicitly reported, never marked PASS.
- Results are only infrastructure-level R2/PDF **transport evidence**. They do **not** establish a real WAFL Label/WorkOrder authorized photo upload and relaunch, PDF persisted grant/revoke/share, iPhone Preview or Apple/Neon parity. Those are separate Owner/provider/device acceptance.
- The new gate is not registered or activated in this Runner PR. In DEV-CONTROL registration use `ready_for_owner: false` until Runner code has exact validated integration, reviewed effects, existing Worker identities and scoped credentials are independently confirmed. Product state stays `DECISION_REQUIRED` until independently authorized product docs/state PR explicitly selects this gate. No button should display as executable solely because this PR passes CI.

## Future scoped Secret bindings (names only; never values)

The existing scoped Cloudflare account token/ID and R2 Worker/bucket/name/URL/Images entitlement/signing parity settings remain unchanged. The code **would additionally require** `WAFL_R2_WORKER_UPLOAD_SECRET` matching the incumbent non-Production Worker HMAC secret, `WAFL_PDF_DEV_WORKER_NAME`, `WAFL_PDF_DEV_WORKER_URL` and `WAFL_PDF_GENERATOR_TOKEN` for the distinct non-Production PDF Worker. Their presence/parity and the PDF Worker deployment are **not known** in this PR and must not be claimed, guessed, provisioned or printed. Establishing/changing any secret or external resource requires separate Owner authorization; no automatic or chat-origin mutation.

## Validation and Owner sequence

1. Runner PR-head validation: targeted offline canary signature/authorization, response integrity, exact cleanup and gate contract tests; normal registered Runner Validation applies. No external Cloudflare or Supabase calls by PR tests.
2. Separate common-system integration under the applicable exact PR/head/CI conditions; current Runner integration alone does not enable Owner action.
3. Independently prepare disabled DEV-CONTROL gate registration and product docs/state PR, with no fake ready flag. Activate only after exact verified external prerequisites and Owner-controlled effect disclosure. Then one separate SANJINWORKS Owner media probe may create/clean synthetic development R2 bytes and render ephemeral PDF bytes. Distinguish its resulting transport PASS from WAFL iPhone functionality PASS.

No change to CLASSMO, ESC, MUVEL, KDN, credentials, SANJINWORKS site source, Production or product repository source in this preparation.
