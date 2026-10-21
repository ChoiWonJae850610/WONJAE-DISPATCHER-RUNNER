## 2026-10-21 — CLASSMO #161 source identity pin, no LIVE admission

Verified registered CLASSMO branch `cloud-dev-v1 e811fd9f28f56d32110e758907089eef84d9fd6b`, PR #161 MERGED and exact PR-head #39530617643 / integrated #39530708592 SUCCESS; previously accepted P2 read-only Provider #39521361262 SUCCESS is reused without rerun. Existing P2 plan Git blob `2aebbaf9dfa2699c60d14e3e614927d6653103c1` and source adapter blob `aad68952da90ba0040f85fa23e790dcd8ed1a19a` stay fixed.

The **existing** `classmo_editorial_027_p2_live_guard_v1` dormant admission now checks three source Git identities together: source adapter, CLASSMO product LIVE host Git blob `ff91544c59d0422aa9f8d4499b0e728db3f134ff` (`scripts/classmo_editorial_p2_live_host.py`), and Edge client Git blob `513665db7655230d2bba1c605cd28d57b518d145` (`apps/mobile/src/presentation/editorial-edge-client.ts`). The exact blobs reside only as inert `source_*_blob` pins in DEV-CONTROL; they are not signed deployed Edge bytes, runtime adapter acceptance, an independently reviewed signer, licensing or customer QA. The dormant gate must **still abort** `CLASSMO_P2_OWNER_RUNTIME_NOT_EXECUTABLE`. No new Runner/Provider dispatch or generic engine.

**Activation remains separate:** verify a genuinely deployed non-Production Edge 1.76.2 runtime and ImageMagick WASM 0.0.44 asset through independent service readback, bind an unguessable runner challenge to the exact source/deployment and verify the runtime signature against a separately reviewed trusted issuer/root, review genuine server-only original-image/license hashes, validate real owner/operator principals and cross-workspace 108 HTTP/Storage cases, PNG/JPEG-EXIF/WebP normalization receipts and negative uploads, two independent PostgreSQL races, rollback and exact synthetic cleanup/non-owned fingerprint. Only then may a separately reviewed DEV-CONTROL activation PR set `ready_for_owner:true`, replace the `source_contract_only` provider with a genuinely executable Development provider, and provide a usable SANJINWORKS Owner LIVE action. Product Source PASS and Readonly PASS are not that authority.

This PR changes only trusted runner dormant source pin verification, one focused offline test and this guide. It makes no Provider/DB/Auth/Edge/Storage/credential/Production changes or product Runner execution.

---

# CLASSMO / WAFL non-Production Owner LIVE runtime handoff

Status: source PREPARED. Existing gates remain inactive with
\`ready_for_owner: false\`. The current product-owned adapters do not yet exist.

## CLASSMO first
The existing P2 plan blob, prior 017/private bucket evidence and offline
controller remain untouched. A new fixed Runner handoff
\`scripts/classmo_p2_live_owner_handoff.py\` uses the exact product source
\`scripts/classmo_editorial_p2_live_owner_adapter.py\` ONLY after that file
is implemented by the authenticated SANJINWORKS Owner product development
action, verified at exact Git blob and activated in DEV-CONTROL.
Real adapter responsibilities: pinned Edge 1.76.2 and WASM 0.0.44 deployed
entrypoint and server-only license proof; real PNG/JPEG-EXIF/WebP bytes,
independent private download/decode/metadata removal and 017 commitment;
108 authenticated role-action checks; independent A/B PostgreSQL lock/barrier
commit/rollback; exact DB/Auth/Storage cleanup and independently zero residue.
Never substitute mocks. The Runner masks child output to avoid secret logging.

## WAFL in parallel
\`scripts/wafl_g1_live_owner_handoff.py\` admits ONLY fixed product adapter
\`scripts/wafl_g1_owner_runtime_adapter.mjs\` after approved exact Git blob,
supported real two-subject Apple Auth, audited one-to-one identity mapping,
real RLS/RPC two sessions, named 46 FK/4 CHECK Neon invariant readbacks,
full data/fingerprint parity and independent cleanup proof.
No Neon credentials or Apple subjects are created, guessed or logged.
The 004/005/006 migrated state, ten-table migration and read-only Provider
success are never replayed by this handoff.

## Admission / verification
Old dormant guards retain their existing executor identity and old abort codes
when \`ready_for_owner: false\`. The active branch/sha and exact integrated CI
are rechecked by existing Runner provider workflow. Future activation requires
a separate product-owned adapter blob in registered control, the exact current
Owner product PROVIDER_GATE, an authenticated SANJINWORKS action and relevant
Development credentials already available. The new scripts refuse unpinned,
absent, changed or mock-only product adapters and incomplete evidence.

This common PR does not modify product source, GitHub Runner permissions,
Provider, credentials, Production, KDN or actual database/storage state.
No new generic Dispatcher, engine or Owner admin screen is introduced.


## 2026-10-18: CLASSMO #149 source identity contract, not LIVE acceptance

The trusted-main scripts/classmo_p2_live_owner_handoff.py source_runtime_contract()
publishes CLASSMO_P2_RUNTIME_HANDOFF_V1 for **source-stage implementation only**:
the fixed CLASSMO Development project, immutable P2 plan blob,
editorial-media-prepare Edge function, Edge Runtime 1.76.2 and ImageMagick WASM
0.0.44. These values describe a future authenticated Owner executor's proof
requirements. They do not prove deployment, signer trust, license approval or
provider authentication. The product source adapter may finish offline
transport, synthetic fixture and fail-closed runtime-verification interfaces
without calling the Provider or producing fake PASS evidence.

Future LIVE admission must bind a fresh trusted Runner challenge to the exact
integrated source SHA, immutable project/plan/adapter blob, independently
observed deployed non-Production function revision and WASM asset digest,
and a cryptographic signature verified against a reviewed independent runtime
signer/trust root. It must separately verify server-only license authorization
against actual processed bytes and normalized receipts; then real Auth roles,
negative HTTP/Storage and upload cases, PostgreSQL A/B lock barriers and exact
synthetic zero-residue/non-owned fingerprint readbacks. JSON "signature: PASS",
"reviewed: true", client flags, mock replies, a source hash alone, or an Owner
session boolean are not admission. The trusted-main
require_registered_runtime_signature() currently always blocks; actual signer,
trust-root registration and independent deployment proof remain separate
Provider-stage work requiring their own authority. No new credential, runtime
configuration or LIVE acceptance is provided here.

Existing CLASSMO source PR #149 already received Owner recovery-doc PR #150 and
then repeatedly returned MANUAL_REQUIRED. To preserve that source and its
prior canonical text, the existing two-file Owner recovery-doc attachment now
accepts exactly one subsequent appended source-scope entry ONLY after reviewing
the prior trusted attachment commit history and unchanged previous scope and
document suffix, the new failed-run identity, one open source PR and the exact
docs-head CI. This remains an authenticated SANJINWORKS Owner action; no product
source merge, Provider launch, new UI, applied SQL 017 or Production operation.

WAFL #95 is already integrated; its G1-V1/V2 LIVE acceptance remains inactive
pending real Apple/identity-map/Neon proof. No WAFL source/gate is changed.
